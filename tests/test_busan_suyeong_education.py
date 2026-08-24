from __future__ import annotations

from collections import Counter
from html import escape
import os
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import municipal_busan_suyeong as suyeong


class _Response:
    def __init__(self, url: str, html: str, status_code: int = 200) -> None:
        self.url = url
        self.text = html
        self.content = html.encode("utf-8")
        self.status_code = status_code
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self.history: list[Any] = []


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target(
    provider: str = suyeong.BUSAN_SUYEONG_PROVIDER,
    url: str = suyeong.BUSAN_SUYEONG_CANONICAL_URL,
) -> dict[str, str]:
    return {"provider": provider, "url": url, "name": "수영구 교육"}


def _local_application_url(identity: str) -> str:
    return (
        f"https://{suyeong.BUSAN_SUYEONG_HOST}"
        f"{suyeong.BUSAN_SUYEONG_APPLICATION_PATH}?"
        "menuCd=DOM_000001801123456001&"
        f"rmenuCd={suyeong.BUSAN_SUYEONG_MENU}&INTEDUNUM={identity}"
    )


def _local_login_application_url(identity: str) -> str:
    nested = (
        "https://www.suyeong.go.kr/reserve/board/write.suyeong?"
        f"boardId={suyeong.BUSAN_SUYEONG_BOARD_ID}%2526"
        f"menuCd={suyeong.BUSAN_SUYEONG_MENU}%2526INTNUM={identity}"
    )
    return (
        "https://www.suyeong.go.kr/reserve/index.suyeong?"
        f"menuCd={suyeong.BUSAN_SUYEONG_LOGIN_MENU}&"
        f"forwardUrl={nested}&returnUrl={nested}"
    )


def _local_card(
    identity: str,
    title: str,
    *,
    source_status: str,
    start: str,
    end: str,
    apply_start: str,
    apply_end: str,
    actionable: bool,
) -> str:
    state = suyeong._LOCAL_STATUS_CLASS[source_status]
    detail_url = suyeong.busan_suyeong_detail_url(identity)
    if actionable:
        control = (
            f'<a href="{escape(_local_application_url(identity), quote=True)}">'
            "강좌신청하기</a>"
        )
    else:
        marker = "대기중입니다" if source_status == "대기중" else "접수가 마감되었습니다"
        control = marker
    definitions = (
        ("교육기간", f"{start} ~ {end}"),
        ("교육장소", "수영구 평생학습관"),
        ("교육시간", "10:00~12:00"),
        ("강의요일", "수"),
        ("신청방법", "온라인 선착순"),
        ("강사명", "SECRET_LOCAL_LIST_INSTRUCTOR 010-1111-2222"),
    )
    fields = "".join(
        f"<dl><dt>{label}</dt><dd>{escape(value)}</dd></dl>"
        for label, value in definitions
    )
    return f"""
      <li><div class="list_box {state}">
        <div class="cate_box"><span class="cate_1">{source_status}</span></div>
        <h5><a href="{escape(detail_url, quote=True)}">{escape(title)}</a></h5>
        <span class="date">{apply_start} ~ {apply_end}</span>
        {fields}<div class="more">{control}</div>
      </div></li>
    """


def _local_page(
    page: int,
    *,
    drift: bool = False,
    bad_sentinel: bool = False,
) -> str:
    cards = ""
    if page == 1 or bad_sentinel:
        current_title = (
            "[평생학습 특강] 변경된 미래 시민교실"
            if drift
            else "[평생학습 특강] 미래 시민교실"
        )
        cards = _local_card(
            "9001",
            current_title,
            source_status="접수중",
            start="2099-08-01",
            end="2099-08-31",
            apply_start="2099-07-01",
            apply_end="2099-07-31",
            actionable=True,
        )
        cards += _local_card(
            "9000",
            "옛 수영구 강좌",
            source_status="교육마감",
            start="2098-08-01",
            end="2098-08-31",
            apply_start="2098-07-01",
            apply_end="2098-07-31",
            actionable=False,
        )
    active = ""
    if page == 1:
        active = (
            '<div class="page"><a class="on" '
            f'href="?boardId={suyeong.BUSAN_SUYEONG_BOARD_ID}&amp;'
            f'menuCd={suyeong.BUSAN_SUYEONG_MENU}&amp;nowPage=1">1</a></div>'
        )
    return f"""
      <html><head><title>{suyeong._LOCAL_LIST_TITLE}</title></head><body>
        <form class="rfc_bbs_searchForm" method="get"
          action="{suyeong.BUSAN_SUYEONG_LIST_PATH}">
          <input name="boardId" value="{suyeong.BUSAN_SUYEONG_BOARD_ID}">
          <input name="menuCd" value="{suyeong.BUSAN_SUYEONG_MENU}">
          <input name="contentsSid" value="{suyeong.BUSAN_SUYEONG_CONTENTS_SID}">
          <input name="startPage" value="1">
        </form>
        <p class="boardPage">총게시물 : 2건 / 페이지 : {page} / 1</p>
        <div class="sub_reserve_box"><ul>{cards}</ul></div>{active}
      </body></html>
    """


def test_local_list_accepts_official_short_application_label() -> None:
    html = _local_page(1).replace(
        f'href="{escape(_local_application_url("9001"), quote=True)}">'
        "강좌신청하기</a>",
        f'href="{escape(_local_login_application_url("9001"), quote=True)}">'
        "접수하기</a>",
        1,
    )

    rows, total, last = suyeong._parse_local_page(
        BeautifulSoup(html, "html.parser"),
        page=1,
    )

    assert total == 2
    assert last == 1
    assert rows[0]["reservation_available"] is True
    assert rows[0]["application_url"] == _local_login_application_url("9001")


def test_local_login_application_control_is_identity_bound() -> None:
    with pytest.raises(
        suyeong.BusanSuyeongContractError,
        match="login application control",
    ):
        suyeong._application_url(_local_login_application_url("9002"), "9001")


def _local_detail(*, wrong_title: bool = False) -> str:
    title = "다른 미래 시민교실" if wrong_title else "미래 시민교실"
    values = (
        ("접수기간", "2099-07-01 ~ 2099-07-31"),
        ("교육기간", "2099-08-01 ~ 2099-08-31"),
        ("시간(요일)", "수 10:00~12:00"),
        ("대상구분", "수영구민"),
        ("수강료", "무료"),
        ("재료비", "없음"),
        ("준비물", "SECRET_LOCAL_PREPARATION"),
        ("강사명", "SECRET_LOCAL_DETAIL_INSTRUCTOR 010-2222-3333"),
        ("대상인원", "SECRET_LOCAL_CAPACITY 20명"),
        ("신청현황", "SECRET_LOCAL_ENROLLMENT 3명"),
    )
    fields = "".join(
        f'<li><span class="name">{label}</span>{escape(value)}</li>'
        for label, value in values
    )
    return f"""
      <html><head><title>{suyeong._LOCAL_DETAIL_TITLE}</title></head><body>
        <div id="content">
          <div class="bbs_vtype edu"><dl class="infor">
            <dt><span class="state st2">접수중</span>[ 2099년 테스트 ] {title}</dt>
            <dd class="edu"><ul>{fields}</ul></dd>
          </dl></div>
          <div class="btn_list2"><span class="btnBs"><a
            href="{escape(_local_application_url('9001'), quote=True)}">
            강좌신청하기</a></span></div>
          <div class="contents">SECRET_LOCAL_FREE_FORM local-private@example.test</div>
          <div class="file">SECRET_LOCAL_ATTACHMENT.hwp</div>
          <table class="applicants"><tr><td>SECRET_LOCAL_APPLICANT</td></tr></table>
        </div>
      </body></html>
    """


def _platform_row(
    sequence: int,
    *,
    identity: str,
    title: str,
    external_url: str = "",
    source_status: str = "교육중",
) -> str:
    if external_url:
        link = f'href="{escape(external_url, quote=True)}" target="_blank"'
    else:
        link = (
            'href="javascript:;" '
            f'onclick="fn_learning_detail(\'{identity}\'); return false;"'
        )
    return f"""
      <tr><td>{sequence}</td>
        <td class="subject"><a {link}><span class="tit">{escape(title)}</span>
          <span class="org">{suyeong.BUSAN_LIFELONG_SUYEONG_OFFICE_NAME}</span></a></td>
        <td><span>무료</span><br><span>SECRET_PLATFORM_LIST_INSTRUCTOR</span></td>
        <td><span class="s_type blue"><em class="hidden">교육기간</em>
          2099.08.01~2099.08.31<pre>목, 14:00~16:00</pre></span></td>
        <td><span class="s_type indigo1"><em class="hidden">모집인원</em>20명</span>
          <span class="s_type red1"><em class="hidden">일반접수</em>
          2099.07.01~2099.07.31 ( 접수인원 : 3 )</span></td>
        <td><span class="s_type2 mint"><em class="hidden">선착순</em></span>
          <span class="s_btn blue">{source_status}</span></td>
        <td><a href="#">보기</a></td></tr>
    """


def _platform_page(
    page: int,
    *,
    drift: bool = False,
    bad_sentinel: bool = False,
    unmatched_external: bool = False,
) -> str:
    if page == 1:
        native_title = "변경된 수영 평생학습 강좌" if drift else "수영 평생학습 강좌"
        external_identity = "9999" if unmatched_external else "9001"
        external = suyeong.busan_suyeong_detail_url(external_identity)
        body = _platform_row(
            2,
            identity="LEARNING_00090001",
            title=native_title,
        )
        body += _platform_row(
            1,
            identity=external,
            title="미래 시민교실",
            external_url=external,
            source_status="접수중",
        )
    elif bad_sentinel:
        body = _platform_row(
            1,
            identity="LEARNING_00090002",
            title="경계를 넘은 플랫폼 강좌",
        )
    else:
        body = '<tr><td colspan="7">등록된 교육강좌가 없습니다.</td></tr>'
    return f"""
      <html><head><title>부산평생학습플랫폼</title></head><body>
        <form id="learningVO" method="post"
          action="{suyeong.BUSAN_LIFELONG_LIST_PATH}">
          <input name="inst_id" value="{suyeong.BUSAN_LIFELONG_SUYEONG_OFFICE}">
          <input name="display_type" value="2"><input name="pageIndex" value="{page}">
          <input name="l_search_ch" value="0">
          <select id="o_search_ch"><option
            value="{suyeong.BUSAN_LIFELONG_SUYEONG_OFFICE}" selected>
            {suyeong.BUSAN_LIFELONG_SUYEONG_OFFICE_NAME}</option></select>
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
    title = "다른 수영 평생학습 강좌" if wrong_title else "수영 평생학습 강좌"
    safe = {
        "강좌분류": "인문교양",
        "교육대상": "부산시민",
        "교육장소": "수영구 평생학습관",
        "총 교육시간": "8시간",
        "교육기간": "2099.08.01 ~ 2099.08.31",
        "교육시간": "목, 14:00~16:00",
        "수강료": "무료",
        "재료비": "없음",
        "우선모집기간": "해당없음",
        "일반모집기간": "2099.07.01 ~ 2099.07.31",
        "모집방법": "온라인 선착순",
        "신청상태": "접수마감",
        "교육상태": "교육중",
        "결제방법": "해당없음",
        "강좌제한": "없음",
    }
    skipped = {
        "회차명": "SECRET_PLATFORM_SESSION",
        "문의전화": "SECRET_PLATFORM_PHONE 051-610-1234",
        "접수인원": "SECRET_PLATFORM_ENROLLMENT 3 / 20",
        "강좌소개": "SECRET_PLATFORM_DESCRIPTION platform-private@example.test",
        "강좌소개 첨부파일": "SECRET_PLATFORM_ATTACHMENT.hwp",
        "강사": "SECRET_PLATFORM_INSTRUCTOR 010-3333-4444",
        "강의계획서": "SECRET_PLATFORM_PLAN.pdf",
        "주의사항": "SECRET_PLATFORM_WARNING",
        "검색키워드": "SECRET_PLATFORM_KEYWORD",
    }
    fields = "".join(
        f"<dl><dt>{label}</dt><dd>{escape(safe.get(label, skipped.get(label, '')))}</dd></dl>"
        for label in suyeong._PLATFORM_DETAIL_REQUIRED_LABELS
    )
    return f"""
      <html><head><title>부산평생학습플랫폼</title></head><body>
        <form id="learningVO" name="learningVO" method="post"
          action="{suyeong.BUSAN_LIFELONG_DETAIL_PATH}?lng_id=LEARNING_00090001">
          <input name="inst_id" value="{suyeong.BUSAN_LIFELONG_SUYEONG_OFFICE}">
          <input name="lng_id" value="LEARNING_00090001">
        </form>
        <h2 class="enrolTit"><span>[{suyeong.BUSAN_LIFELONG_SUYEONG_OFFICE_NAME}]</span>
          {escape(title)}</h2>
        <div class="form_group">{fields}</div>
      </body></html>
    """


def _city_card(*, title: str, wrong_owner: bool = False) -> str:
    branch = "서구 다른동 주민자치회" if wrong_owner else "수영구 망미2동 주민자치회"
    values = (
        ("기관", branch),
        ("대상", "제한없음"),
        ("장소", "망미2동 프로그램실"),
        (
            "일자",
            "[신청] 2099-07-01 ~ 2099-07-31 "
            "[행사] 2099-08-01 ~ 2099-08-31",
        ),
        ("방법", "방문(선착순)"),
        ("문의", "SECRET_CITY_CARD_PHONE 051-610-9999"),
    )
    fields = "".join(
        f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in values
    )
    return f"""
      <li><a class="reserveItem" href="javascript:void(0);"
        onclick="fn_viewProgrm('301', '9003');return false;">
        <div class="infoBox"><p class="tit" title="{escape(title)}">
          [권역]{escape(title)}</p><span class="statusMark possible">접수중</span>
          <dl>{fields}</dl></div></a></li>
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
            title="변경된 주민자치 생활요가" if drift else "주민자치 생활요가",
            wrong_owner=wrong_owner,
        )
    reserve_list = f'<ul class="reserveList">{cards}</ul>' if cards else ""
    return f"""
      <html><head><title>{suyeong._CITY_LIST_TITLE}</title></head><body>
        <form id="srchForm" name="srchForm" method="get" action="/lctre">
          <input name="curPage" value="{page}">
          <select name="srchGugun"><option value="12" selected>수영구</option></select>
          <select name="srchResveInsttCd"><option value="33" selected>주민자치회</option></select>
        </form>{reserve_list}
        <div class="paginate"><a class="pgEnd"
          href="/lctre/list?curPage=1&amp;srchGugun=12&amp;srchResveInsttCd=33">마지막</a></div>
      </body></html>
    """


def _city_detail(*, wrong_identity: bool = False) -> str:
    program = "9999" if wrong_identity else "9003"
    values = (
        ("운영기간", "2099-08-01(토) ~ 2099-08-31(월)"),
        ("신청기간", "2099-07-01(수) 09:00 ~ 2099-07-31(금) 18:00"),
        ("취소여부", "취소 가능"),
        ("신청방법", "방문(선착순)"),
        ("수강료", "0 원"),
        ("요일 /시간", "화 / 10:00 ~ 12:00"),
        ("문의전화", "SECRET_CITY_DETAIL_PHONE 051-610-8888"),
        ("운영기관", "수영구 망미2동 주민자치회"),
        ("대상", "제한없음"),
        ("첨부파일", "SECRET_CITY_ATTACHMENT.hwp"),
    )
    fields = "".join(
        f"<dl><dt>{escape(label)}</dt><dd>{escape(value)}</dd></dl>"
        for label, value in values
    )
    return f"""
      <html><head><title>{suyeong._CITY_LIST_TITLE}</title></head><body>
        <form id="viewForm" method="post">
          <input name="resveGroupSn" value="301"><input name="progrmSn" value="{program}">
          <div class="contHeader"><h3 class="titPage">[권역]주민자치 생활요가
            <span class="statusMark possible">접수중</span></h3></div>
          <div class="reserveStateWrap">
            <div class="reserveStateInfo">{fields}</div>
            <div class="reserveBtnWrap"><a class="btnTypeXL">방문예약</a></div>
          </div>
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
        wrong_city_owner: bool = False,
        wrong_local_title: bool = False,
        wrong_platform_title: bool = False,
        wrong_city_identity: bool = False,
    ) -> None:
        self.bad_local_sentinel = bad_local_sentinel
        self.bad_platform_sentinel = bad_platform_sentinel
        self.bad_city_sentinel = bad_city_sentinel
        self.local_drift = local_drift
        self.platform_drift = platform_drift
        self.city_drift = city_drift
        self.unmatched_external = unmatched_external
        self.wrong_city_owner = wrong_city_owner
        self.wrong_local_title = wrong_local_title
        self.wrong_platform_title = wrong_platform_title
        self.wrong_city_identity = wrong_city_identity
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
        if parsed.hostname == suyeong.BUSAN_SUYEONG_HOST:
            if parsed.path == suyeong.BUSAN_SUYEONG_LIST_PATH:
                page = int((query.get("nowPage") or ["1"])[0])
                count = self._record(f"local-list-{page}", url)
                return _Response(
                    url,
                    _local_page(
                        page,
                        drift=self.local_drift and page == 1 and count >= 2,
                        bad_sentinel=self.bad_local_sentinel,
                    ),
                )
            if parsed.path == suyeong.BUSAN_SUYEONG_DETAIL_PATH:
                identity = (query.get("dataSid") or [""])[0]
                self._record(f"local-detail-{identity}", url)
                return _Response(url, _local_detail(wrong_title=self.wrong_local_title))
            raise AssertionError("district application/private screens must never be fetched")
        if parsed.hostname == "lll.busan.go.kr":
            if parsed.path == suyeong.BUSAN_LIFELONG_LIST_PATH:
                page = int((query.get("pageIndex") or ["1"])[0])
                count = self._record(f"platform-list-{page}", url)
                return _Response(
                    url,
                    _platform_page(
                        page,
                        drift=self.platform_drift and page == 1 and count >= 2,
                        bad_sentinel=self.bad_platform_sentinel,
                        unmatched_external=self.unmatched_external,
                    ),
                )
            if parsed.path == suyeong.BUSAN_LIFELONG_DETAIL_PATH:
                identity = (query.get("lng_id") or [""])[0]
                self._record(f"platform-detail-{identity}", url)
                return _Response(
                    url,
                    _platform_detail(wrong_title=self.wrong_platform_title),
                )
            raise AssertionError("lifelong application/private screens must never be fetched")
        if parsed.hostname == suyeong.BUSAN_CITY_HOST:
            if parsed.path == suyeong.BUSAN_CITY_LIST_PATH:
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
            if parsed.path == suyeong.BUSAN_CITY_DETAIL_PATH:
                self._record("city-detail", url)
                return _Response(url, _city_detail(wrong_identity=self.wrong_city_identity))
        raise AssertionError(f"unexpected fetch {url}")


def _collect(backend: _Backend, **kwargs: Any):
    return suyeong.collect_busan_suyeong_education(
        _target(),
        timeout=5,
        max_pages=kwargs.pop("max_pages", 3),
        detail_limit=kwargs.pop("detail_limit", 3),
        max_requests=kwargs.pop("max_requests", 40),
        today="2099-07-20",
        fetcher=backend.fetch,
        session_factory=backend.session,
        sleeper=lambda _seconds: None,
        max_workers=1,
        **kwargs,
    )


def test_provider_candidates_ownership_and_audit_are_exact() -> None:
    assert suyeong.BUSAN_SUYEONG_PROVIDER == "MUNI_WWW_SUYEONG_GO_KR_41E9DDEB"
    assert suyeong.BUSAN_SUYEONG_INFORMATION_PROVIDER == "MUNI_WWW_SUYEONG_GO_KR_4A5037DF"
    assert suyeong.BUSAN_CITY_SUYEONG_PROVIDER == "MUNI_RESERVE_BUSAN_GO_KR_3A0E6D4C"
    assert suyeong.BUSAN_LIFELONG_PROVIDER == "MUNI_LLL_BUSAN_GO_KR_944C621B"
    assert suyeong.BUSAN_SUYEONG_CANDIDATE_IDS == {
        "canonical_complete_education_ledger": "MUNI_IR_9276D4CB08F2",
        "registered_lifelong_home": "MUNI_IR_3EA7894865CF",
        "lifelong_information_page": "MUNI_IR_63794D8CB3BB",
        "busan_resident_councils": "MUNI_IR_B21AE03DCD52",
        "busan_lifelong_federation": "MUNI_IR_4332B8F8A6D7",
        "wrong_municipality_museum_detail": "MUNI_IR_2BA97ED12CEB",
    }
    audit = suyeong.BUSAN_SUYEONG_OWNER_BOUNDARY_AUDIT
    assert audit[suyeong.BUSAN_SUYEONG_PROVIDER]["registered_url"] == suyeong.BUSAN_SUYEONG_HOME_URL
    assert audit[suyeong.BUSAN_SUYEONG_PROVIDER]["canonical_url"] == suyeong.BUSAN_SUYEONG_CANONICAL_URL
    assert audit[suyeong.BUSAN_CITY_SUYEONG_PROVIDER]["filter"] == {
        "srchGugun": "12",
        "srchResveInsttCd": "33",
    }
    assert audit[suyeong.BUSAN_LIFELONG_PROVIDER]["office_code"] == "OFFICE_00002661"
    office = suyeong._platform_office()
    assert office.name == "수영구청"
    assert office.ownership == "duplicate_dedicated_suyeong_owner"
    assert office.municipality_code == "2650000000"
    discovery = suyeong.BUSAN_SUYEONG_DISCOVERY_AUDIT
    assert discovery["district_rows"] == 1730
    assert discovery["district_data_pages"] == 193
    assert discovery["platform_rows"] == 108
    assert discovery["platform_external_rows"] == 100
    assert discovery["platform_native_rows"] == 8
    assert discovery["resident_rows"] == 35
    assert discovery["unique_education_source_rows"] == 1773
    assert discovery["atomic_current_rows"] == 117
    assert discovery["atomic_required_requests_without_retries"] == 323


def test_exact_target_url_builders_external_identity_and_anomalies() -> None:
    assert suyeong.is_busan_suyeong_education_target(_target())
    assert suyeong.is_busan_suyeong_education_target(
        _target(url=suyeong.BUSAN_SUYEONG_HOME_URL)
    )
    assert not suyeong.is_busan_suyeong_education_target(
        _target(url=suyeong.BUSAN_SUYEONG_CANONICAL_URL + "&category=1")
    )
    assert suyeong.busan_suyeong_list_url(2).endswith(
        "boardId=BBS_0000152&menuCd=DOM_000001801001000000&nowPage=2"
    )
    assert suyeong.busan_suyeong_city_list_url(3).endswith(
        "curPage=3&srchGugun=12&srchResveInsttCd=33"
    )
    platform_query = parse_qs(
        urlparse(suyeong.busan_suyeong_lifelong_list_url(1)).query
    )
    assert platform_query["pageUnit"] == ["1000"]
    detail = suyeong.busan_suyeong_detail_url("9001")
    assert suyeong.canonical_busan_suyeong_course_identity(detail) == "9001"
    assert suyeong.canonical_busan_suyeong_course_identity(detail + "&x=1") == ""
    assert suyeong._audited_optional_range(
        "2024-12-16 ~", identity="284930", kind="application"
    ) == ("", "", True)
    with pytest.raises(suyeong.BusanSuyeongContractError):
        suyeong._audited_optional_range(
            "2024-12-16 ~", identity="999999", kind="application"
        )
    with pytest.raises(suyeong.BusanSuyeongContractError):
        suyeong.busan_suyeong_list_url(True)
    with pytest.raises(suyeong.BusanSuyeongContractError):
        suyeong.busan_suyeong_city_detail_url("301", "https://evil.example")


def test_individual_parsers_preserve_safe_fields_and_skip_private_values() -> None:
    local_rows, total, last = suyeong._parse_local_page(
        BeautifulSoup(_local_page(1), "lxml"), page=1
    )
    assert (total, last) == (2, 1)
    assert local_rows[0]["title"] == "미래 시민교실"
    assert local_rows[0]["branch"] == "평생학습 특강"
    assert local_rows[1]["branch"] == "미분류 교육프로그램"
    local = suyeong._parse_local_detail(
        BeautifulSoup(_local_detail(), "lxml"),
        local_rows[0]["raw_url"],
        local_rows[0],
    )
    assert local["target"] == "수영구민"
    assert local["reservation_available"] is True

    platform_rows, platform_last = suyeong._parse_platform_page(
        BeautifulSoup(_platform_page(1), "lxml"), page=1
    )
    assert platform_last == 1
    assert {row["raw_fields"]["identity_kind"] for row in platform_rows} == {
        "internal",
        "external",
    }
    city_rows, city_last = suyeong._parse_city_page(
        BeautifulSoup(_city_page(1), "lxml"), page=1
    )
    assert city_last == 1
    assert city_rows[0]["title"] == "주민자치 생활요가"
    assert city_rows[0]["branch"] == "수영구 망미2동 주민자치회"
    assert "SECRET_" not in repr((local_rows, local, platform_rows, city_rows))


def test_atomic_three_ledger_snapshot_duplicate_suppression_and_privacy() -> None:
    backend = _Backend()
    rows, parser, meta = _collect(backend)

    assert parser == suyeong.BUSAN_SUYEONG_PARSER
    assert [row["provider_course_id"] for row in rows] == [
        f"{suyeong.BUSAN_SUYEONG_PROVIDER}:district:9001",
        f"{suyeong.BUSAN_SUYEONG_PROVIDER}:lifelong:LEARNING_00090001",
        f"{suyeong.BUSAN_SUYEONG_PROVIDER}:reserve:301:9003",
    ]
    assert meta["district_source_rows"] == 2
    assert meta["platform_source_rows"] == 2
    assert meta["platform_external_duplicate_rows"] == 1
    assert meta["platform_native_rows"] == 1
    assert meta["city_source_rows"] == 1
    assert meta["source_total"] == 5
    assert meta["unique_education_source_rows"] == 4
    assert meta["district_current_count"] == 1
    assert meta["platform_native_current_count"] == 1
    assert meta["city_current_count"] == 1
    assert meta["current_source_count"] == 3
    assert meta["expired_count"] == 1
    assert meta["required_list_requests"] == 11
    assert meta["list_requests"] == 11
    assert meta["sentinel_requests"] == 3
    assert meta["stability_rechecks"] == 5
    assert meta["detail_pages"] == 3
    assert meta["network_requests"] == 14
    assert meta["status_counts"] == {"OPEN": 2, "CLOSED": 1}
    assert meta["application_control_count"] == 1
    assert meta["offline_application_count"] == 1
    assert meta["duplicate_source_identity_count"] == 1
    assert meta["privacy_redactions"] > 0
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["atomic_union_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert {row["raw_fields"]["source_catalog"] for row in rows} == {
        "suyeong_complete_district_education",
        "busan_lifelong_suyeong_native",
        "busan_reserve_suyeong_resident_centres",
    }
    assert all(row["municipality_code"] == "2650000000" for row in rows)
    city = next(row for row in rows if ":reserve:" in row["provider_course_id"])
    assert city["application_type"] == "OFFLINE_APPLY"
    assert city["reservation_available"] is False
    assert not any("/reserve/index.suyeong" in url for url in backend.urls)

    serialized = repr(rows)
    for secret in (
        "SECRET_LOCAL_LIST_INSTRUCTOR",
        "SECRET_LOCAL_PREPARATION",
        "SECRET_LOCAL_DETAIL_INSTRUCTOR",
        "SECRET_LOCAL_CAPACITY",
        "SECRET_LOCAL_ENROLLMENT",
        "SECRET_LOCAL_FREE_FORM",
        "SECRET_LOCAL_ATTACHMENT",
        "SECRET_LOCAL_APPLICANT",
        "SECRET_PLATFORM_LIST_INSTRUCTOR",
        "SECRET_PLATFORM_SESSION",
        "SECRET_PLATFORM_PHONE",
        "SECRET_PLATFORM_ENROLLMENT",
        "SECRET_PLATFORM_DESCRIPTION",
        "SECRET_PLATFORM_ATTACHMENT",
        "SECRET_PLATFORM_INSTRUCTOR",
        "SECRET_PLATFORM_PLAN",
        "SECRET_PLATFORM_WARNING",
        "SECRET_PLATFORM_KEYWORD",
        "SECRET_CITY_CARD_PHONE",
        "SECRET_CITY_DETAIL_PHONE",
        "SECRET_CITY_ATTACHMENT",
        "SECRET_CITY_FREE_FORM",
        "local-private@example.test",
        "platform-private@example.test",
        "city-private@example.test",
    ):
        assert secret not in serialized
    assert all(row["raw_fields"]["application_form_fetched"] is False for row in rows)


@pytest.mark.parametrize(
    "flag",
    (
        "bad_local_sentinel",
        "bad_platform_sentinel",
        "bad_city_sentinel",
        "local_drift",
        "platform_drift",
        "city_drift",
        "unmatched_external",
        "wrong_city_owner",
        "wrong_local_title",
        "wrong_platform_title",
        "wrong_city_identity",
    ),
)
def test_any_source_contract_failure_discards_atomic_union(flag: str) -> None:
    rows, _parser, meta = _collect(_Backend(**{flag: True}))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"]


def test_caps_dedupe_and_wrong_target_fail_closed() -> None:
    rows, _parser, meta = _collect(_Backend(), max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(_Backend(), detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(_Backend(), max_requests=10)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "request cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(
        _Backend(), dedupe_rows=lambda values: values[:2]
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]

    backend = _Backend()
    rows, _parser, meta = suyeong.collect_busan_suyeong_education(
        _target(url=suyeong.BUSAN_CITY_SUYEONG_URL),
        fetcher=backend.fetch,
        session_factory=backend.session,
    )
    assert rows == []
    assert backend.urls == []
    assert "exact registered/canonical" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_MUNICIPAL_AUDIT") != "1",
    reason="set RUN_LIVE_MUNICIPAL_AUDIT=1 for the 323-request Suyeong audit",
)
def test_live_exact_snapshot_matches_2026_07_22_audit() -> None:
    rows, parser, meta = suyeong.collect_busan_suyeong_education(
        _target(), today="2026-07-22"
    )
    assert parser == suyeong.BUSAN_SUYEONG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["atomic_union_complete"] is True
    assert meta["district_source_rows"] == 1730
    assert meta["district_data_pages"] == 193
    assert meta["district_current_count"] == 77
    assert meta["platform_source_rows"] == 108
    assert meta["platform_external_duplicate_rows"] == 100
    assert meta["platform_current_external_duplicate_rows"] == 11
    assert meta["platform_native_rows"] == 8
    assert meta["platform_native_current_count"] == 5
    assert meta["platform_semantic_censuses"] == 2
    assert meta["city_source_rows"] == 35
    assert meta["city_data_pages"] == 4
    assert meta["city_current_count"] == 35
    assert meta["source_total"] == 1873
    assert meta["unique_education_source_rows"] == 1773
    assert meta["current_source_count"] == 117
    assert meta["detail_pages"] == 117
    assert meta["returned_count"] == 117
    assert meta["status_counts"] == {"CLOSED": 87, "SCHEDULED": 11, "OPEN": 19}
    assert meta["application_control_count"] == 18
    assert meta["offline_application_count"] == 1
    assert meta["branch_count"] == 11
    assert meta["required_list_requests"] == 206
    assert meta["network_requests"] >= 323
    assert meta["network_requests"] <= 450
    assert len(rows) == 117
    assert len({row["provider_course_id"] for row in rows}) == 117
