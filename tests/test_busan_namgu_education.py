from __future__ import annotations

from collections import Counter
from html import escape
import os
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import municipal_busan_namgu as namgu


class _Response:
    def __init__(self, url: str, html: str, status_code: int = 200):
        self.url = url
        self.content = html.encode("utf-8")
        self.status_code = status_code
        self.history: list[Any] = []


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target(
    provider: str = namgu.BUSAN_NAMGU_PROVIDER,
    url: str = namgu.BUSAN_NAMGU_CANONICAL_URL,
) -> dict[str, str]:
    return {"provider": provider, "url": url, "name": "부산 남구 교육"}


def _local_card(
    identity: str,
    title: str,
    *,
    page: int,
    start: str,
    end: str,
    active: bool,
) -> str:
    status = "접수중" if active else "접수마감"
    fields = (
        ("접수기간", "2099-07-01 ~ 2099-07-31"),
        ("교육기간", f"{start} ~ {end}"),
        ("교육장소", "남구평생학습관 1강의실"),
        ("모집인원", "총 20명"),
        ("접수현황", "3 / 20명 (대기 : 0명)"),
        ("접수방법", "온라인"),
    )
    values = "".join(
        f"<li><strong>{escape(label)}</strong>{escape(value)}</li>"
        for label, value in fields
    )
    href = (
        f"/edu/sub/sub.php?menucd=A0005&idx={identity}&sort1=info&pn={page}"
        "&se1=&se2=&se3=&se4=&se5=&key=&key_name=&trLectureOption="
    )
    return f"""
      <li><a href="{escape(href, quote=True)}">
        <span class="ty2">{status}</span>
        <p class="s-title">{escape(title)}</p>
        <ul class="info">{values}</ul>
      </a></li>
    """


def _local_page(
    page: int,
    *,
    drift: bool = False,
    bad_sentinel: bool = False,
) -> str:
    cards = ""
    if page == 1 or bad_sentinel:
        title = "변경된 미래 시민교실" if drift else "미래 시민교실"
        cards = _local_card(
            "9001",
            title,
            page=page,
            start="2099-08-01",
            end="2099-08-31",
            active=True,
        )
        cards += _local_card(
            "9000",
            "지난 시민교실",
            page=page,
            start="2098-08-01",
            end="2098-08-31",
            active=False,
        )
    active = '<a class="on">1</a>' if page == 1 else ""
    return f"""
      <html><head><title>강좌신청 &gt; 일반강좌</title></head><body>
        <form id="boxsel2" name="boxsel2" method="get"
          action="/edu//sub/sub.php">
          <input name="menucd" value="A0005">
        </form>
        <p class="page_num">총 2건의 게시물이 있습니다.</p>
        <div class="multiPurpose-list"><ul>{cards}</ul></div>
        <p class="pageing">{active}<a class="btn_end"
          href="/edu/sub/sub.php?menucd=A0005&amp;pn=1">끝</a></p>
      </body></html>
    """


def _local_detail(*, wrong_title: bool = False) -> str:
    title = "다른 시민교실" if wrong_title else "미래 시민교실"
    values = {
        "사업분류": "평생학습",
        "교육기간": "2099-08-01 ~ 2099-08-31",
        "교육시간": "화 10:00~12:00",
        "교육대상": "남구민",
        "정원": "20명",
        "교육장소": "남구평생학습관 1강의실",
        "수강료": "무료",
        "교육방법": "대면",
        "접수기간": "2099-07-01 ~ 2099-07-31",
        "접수방법": "온라인",
        "강사명": "SECRET_LOCAL_INSTRUCTOR 010-1111-2222",
        "문의전화": "SECRET_LOCAL_PHONE 051-607-0000",
        "접수메일": "SECRET_LOCAL_EMAIL private@example.test",
        "FAX번호": "SECRET_LOCAL_FAX 051-607-9999",
        "강좌소개": "SECRET_LOCAL_DESCRIPTION",
        "강좌소개이미지": "SECRET_LOCAL_IMAGE.jpg",
        "유의사항": "SECRET_LOCAL_NOTICE",
        "강의계획서": "SECRET_LOCAL_PLAN.pdf",
        "수강신청서": "SECRET_LOCAL_APPLICATION.hwp",
        "기타": "SECRET_LOCAL_OTHER",
    }
    order = (
        "사업분류",
        "교육기간",
        "교육시간",
        "교육대상",
        "정원",
        "교육장소",
        "수강료",
        "교육방법",
        "접수기간",
        "접수방법",
        "강사명",
        "문의전화",
        "접수메일",
        "FAX번호",
        "강좌소개",
        "강좌소개이미지",
        "유의사항",
        "강의계획서",
        "수강신청서",
        "기타",
    )
    rows = "".join(
        f"<tr><th>{label}</th><td>{escape(values[label])}</td></tr>"
        for label in order
    )
    return f"""
      <html><head><title>강좌신청 &gt; 일반강좌 &gt; {escape(title)}</title></head>
      <body>
        <div class="edu_tit"><div class="subject">{escape(title)}</div>
          <div class="sang_type1"><span>접수중</span></div></div>
        <div class="tbl-wrap"><table class="tbl"><tbody>{rows}</tbody></table></div>
        <div class="taC mT30"><a
          href="/edu/sub/sub.php?menucd=A0005&amp;idx=9001&amp;sort1=apply">
          수강신청</a><a href="#">목록</a></div>
        <div>SECRET_LOCAL_FREE_FORM freeform@example.test</div>
      </body></html>
    """


def _platform_row(
    sequence: int,
    *,
    identity: str,
    title: str,
    external_url: str = "",
    source_status: str = "접수중",
) -> str:
    if external_url:
        title_action = f'href="{escape(external_url, quote=True)}" target="_blank"'
        action = (
            f'<a href="{escape(external_url, quote=True)}">'
            '<span class="button">수강신청</span></a>'
        )
    else:
        onclick = f"fn_learning_detail('{identity}'); return false;"
        title_action = f'href="javascript:;" onclick="{onclick}"'
        action = (
            f'<a href="javascript:;" onclick="{onclick}">'
            '<span class="button">수강신청</span></a>'
        )
    return f"""
      <tr>
        <td>{sequence}</td>
        <td class="subject"><a {title_action}>
          <span class="tit">{escape(title)}</span>
          <span class="org">남구청</span>
        </a></td>
        <td class="type"><span>무료</span><br><span>SECRET_LIST_INSTRUCTOR</span></td>
        <td><span class="s_type blue"><em class="hidden">교육기간</em>
          2099.08.01~2099.08.31<pre>수, 14:00~16:00</pre></span></td>
        <td><span class="s_type indigo1"><em class="hidden">모집인원</em>20명</span>
          <span class="s_type red1"><em class="hidden">일반접수</em>
          2099.07.01~2099.07.31 ( 접수인원 : 3 )</span></td>
        <td><span class="s_type2 mint"><em class="hidden">선착순</em></span>
          <span class="s_btn blue">{source_status}</span></td>
        <td>{action}</td>
      </tr>
    """


def _platform_page(
    page: int,
    *,
    drift: bool = False,
    bad_sentinel: bool = False,
    unmatched_external: bool = False,
    waitlist: bool = False,
) -> str:
    body = ""
    if page == 1 or bad_sentinel:
        native_title = "변경된 남구 인문학" if drift else "남구 인문학"
        alias_idx = "9999" if unmatched_external else "9001"
        external = namgu.busan_namgu_detail_url(alias_idx)
        body = _platform_row(
            2,
            identity="LEARNING_00090001",
            title=native_title,
            source_status="대기접수" if waitlist else "접수중",
        )
        body += _platform_row(
            1,
            identity=external,
            title="미래 시민교실 외부연계",
            external_url=external,
        )
    else:
        body = '<tr><td colspan="7">등록된 교육강좌가 없습니다.</td></tr>'
    return f"""
      <html><head><title>부산평생학습플랫폼</title></head><body>
        <form id="learningVO" method="post"
          action="{namgu.BUSAN_LIFELONG_LIST_PATH}">
          <input name="inst_id" value="{namgu.BUSAN_LIFELONG_NAMGU_OFFICE}">
          <input name="display_type" value="2"><input name="pageIndex" value="{page}">
          <input name="l_search_ch" value="0">
          <select id="o_search_ch"><option
            value="{namgu.BUSAN_LIFELONG_NAMGU_OFFICE}" selected>남구청</option></select>
          <select id="learning_state"><option value="0" selected>전체</option></select>
        </form>
        <table><thead><tr>
          <th>번호</th><th>강좌명 / 교육기관</th><th>재료비 / 강사</th>
          <th>교육기간 / 교육시간</th><th>신청기간 / 접수인원 / 대기자</th>
          <th>상태</th><th>보기</th>
        </tr></thead><tbody>{body}</tbody></table>
        <a class="page_nextend" href="?pageIndex=1"
          onclick="fn_list(1,'');return false;">마지막</a>
      </body></html>
    """


def _platform_detail(
    *,
    wrong_title: bool = False,
    waitlist: bool = False,
    priority: bool = False,
) -> str:
    title = "다른 남구 인문학" if wrong_title else "남구 인문학"
    safe_values = {
        "강좌분류": "인문교양",
        "교육대상": "부산시민",
        "교육장소": "남구 평생학습관",
        "총 교육시간": "8시간",
        "교육기간": "2099.08.01 ~ 2099.08.31",
        "교육시간": "수, 14:00~16:00",
        "수강료": "무료",
        "재료비": "없음",
        "우선모집기간": "해당없음",
        "일반모집기간": "2099.07.01 ~ 2099.07.31",
        "모집방법": "온라인 선착순",
        "신청상태": "대기접수" if waitlist else "일반 접수중",
        "교육상태": "교육대기",
        "결제방법": "해당없음",
        "강좌제한": "없음",
    }
    skipped_values = {
        "회차명": "SECRET_SESSION",
        "문의전화": "SECRET_PLATFORM_PHONE 051-607-1234",
        "접수인원": "SECRET_ENROLLMENT 3 / 20",
        "강좌소개": "SECRET_PLATFORM_DESCRIPTION private@example.test",
        "강좌소개 첨부파일": "SECRET_PLATFORM_ATTACHMENT.hwp",
        "강사": "SECRET_PLATFORM_INSTRUCTOR 010-2222-3333",
        "강의계획서": "SECRET_PLATFORM_PLAN.pdf",
        "주의사항": "SECRET_PLATFORM_WARNING",
        "검색키워드": "SECRET_PLATFORM_KEYWORD",
    }
    definitions = []
    for label in namgu._PLATFORM_DETAIL_REQUIRED_LABELS:
        value = safe_values.get(label, skipped_values.get(label, ""))
        definitions.append(f"<dl><dt>{label}</dt><dd>{escape(value)}</dd></dl>")
    definitions.append(
        "<dl><dt>직장인 여부</dt><dd>SECRET_WORKPLACE_ELIGIBILITY</dd></dl>"
    )
    control = (
        "대기자신청"
        if waitlist
        else "우선모집신청"
        if priority
        else "일반모집신청"
    )
    return f"""
      <html><head><title>부산평생학습플랫폼</title></head><body>
        <form id="learningVO" name="learningVO" method="post"
          action="{namgu.BUSAN_LIFELONG_DETAIL_PATH}?lng_id=LEARNING_00090001">
          <input name="inst_id" value="{namgu.BUSAN_LIFELONG_NAMGU_OFFICE}">
          <input name="lng_id" value="LEARNING_00090001">
        </form>
        <h2 class="enrolTit"><span>[남구청]</span>{escape(title)}</h2>
        <div class="form_group">{''.join(definitions)}</div>
        <a id="learning_aply_btn"
          onclick="fn_learning_apply(); return false;">{control}</a>
      </body></html>
    """


def _city_card(
    *,
    title: str = "주민센터 생활요가",
    branch: str = "남구 대연4동 주민자치회",
) -> str:
    values = (
        ("기관", branch),
        ("대상", "제한없음"),
        ("장소", "대연4동 프로그램실"),
        (
            "일자",
            "[신청] 2099-07-01 ~ 2099-07-31 "
            "[행사] 2099-08-01 ~ 2099-08-31",
        ),
        ("방법", "온라인(선착순)"),
        ("문의", "SECRET_CITY_CARD_PHONE 051-607-9999"),
    )
    definitions = "".join(
        f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in values
    )
    return f"""
      <li><a class="reserveItem" href="javascript:void(0);"
        onclick="fn_viewProgrm('157', '24935');return false;">
        <div class="infoBox"><p class="tit" title="{escape(title)}">{escape(title)}</p>
          <span class="statusMark possible">접수중</span><dl>{definitions}</dl>
        </div>
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
            branch=(
                "서구 다른동 주민자치회"
                if wrong_owner
                else "남구 대연4동 주민자치회"
            ),
        )
    reserve_list = f'<ul class="reserveList">{cards}</ul>' if cards else ""
    return f"""
      <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head><body>
        <form id="srchForm" name="srchForm" method="get" action="/lctre">
          <input name="curPage" value="{page}">
          <select name="srchGugun"><option value="4" selected>남구</option></select>
          <select name="srchResveInsttCd"><option value="33" selected>주민자치회</option></select>
        </form>
        {reserve_list}
        <div class="paginate"><a class="pgEnd"
          href="?curPage=1&amp;srchGugun=4&amp;srchResveInsttCd=33">마지막</a></div>
      </body></html>
    """


def _city_detail(*, wrong_identity: bool = False) -> str:
    program = "99999" if wrong_identity else "24935"
    values = (
        ("운영기간", "2099-08-01(토) ~ 2099-08-31(월)"),
        ("신청기간", "2099-07-01(수) 09:00 ~ 2099-07-31(금) 18:00"),
        ("취소여부", "취소 가능"),
        ("신청방법", "온라인(선착순)"),
        ("수강료", "0 원"),
        ("요일 /시간", "화 / 10:00 ~ 12:00"),
        ("문의전화", "SECRET_CITY_DETAIL_PHONE 051-607-8888"),
        ("운영기관", "남구 대연4동 주민자치회"),
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
          <input name="resveGroupSn" value="157">
          <input name="progrmSn" value="{program}">
          <div class="contHeader"><h3 class="titPage">주민센터 생활요가
            <span class="statusMark possible">접수중</span></h3></div>
          <div class="reserveStateWrap">
            <div class="reserveStateInfo">{definitions}</div>
            <div class="reserveBtnWrap"><a class="btnTypeXL" href="#">예약하기</a></div>
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
        transient_local_detail: bool = False,
        waitlist: bool = False,
        priority: bool = False,
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
        self.transient_local_detail = transient_local_detail
        self.waitlist = waitlist
        self.priority = priority
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
        if parsed.hostname == namgu.BUSAN_NAMGU_HOST:
            identity = (query.get("idx") or [""])[0]
            if identity:
                count = self._record(f"local-detail-{identity}", url)
                if self.transient_local_detail and count == 1:
                    return _Response(
                        url,
                        "<html><head><title>temporary error</title></head>"
                        "<body>temporary</body></html>",
                    )
                return _Response(
                    url,
                    _local_detail(wrong_title=self.wrong_local_title),
                )
            page = int((query.get("pn") or ["1"])[0])
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
            if parsed.path == namgu.BUSAN_LIFELONG_LIST_PATH:
                page = int((query.get("pageIndex") or ["1"])[0])
                count = self._record(f"platform-list-{page}", url)
                return _Response(
                    url,
                    _platform_page(
                        page,
                        drift=self.platform_drift and page == 1 and count >= 2,
                        bad_sentinel=self.bad_platform_sentinel,
                        unmatched_external=self.unmatched_external,
                        waitlist=self.waitlist,
                    ),
                )
            if parsed.path == namgu.BUSAN_LIFELONG_DETAIL_PATH:
                identity = (query.get("lng_id") or [""])[0]
                self._record(f"platform-detail-{identity}", url)
                return _Response(
                    url,
                    _platform_detail(
                        wrong_title=self.wrong_platform_title,
                        waitlist=self.waitlist,
                        priority=self.priority,
                    ),
                )
            raise AssertionError("lifelong applicant/list screens must never be fetched")
        if parsed.hostname == namgu.BUSAN_CITY_HOST:
            if parsed.path == namgu.BUSAN_CITY_LIST_PATH:
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
            if parsed.path == namgu.BUSAN_CITY_DETAIL_PATH:
                self._record("city-detail", url)
                return _Response(
                    url,
                    _city_detail(wrong_identity=self.wrong_city_identity),
                )
        raise AssertionError(f"unexpected fetch {url}")


def _collect(backend: _Backend, **kwargs: Any):
    return namgu.collect_busan_namgu_education(
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


def test_provider_candidates_owner_boundaries_and_live_audit_are_exact() -> None:
    assert namgu.BUSAN_NAMGU_PROVIDER == "MUNI_WWW_BSNAMGU_GO_KR_664BF631"
    assert namgu.BUSAN_CITY_NAMGU_PROVIDER == "MUNI_RESERVE_BUSAN_GO_KR_6FF7EAF5"
    assert namgu.BUSAN_LIFELONG_PROVIDER == "MUNI_LLL_BUSAN_GO_KR_944C621B"
    assert set(namgu.BUSAN_NAMGU_CANDIDATE_IDS.values()) == {
        "MUNI_IR_44E747C4FA57",
        "MUNI_IR_4B0EC6133F42",
        "MUNI_IR_4332B8F8A6D7",
    }
    audit = namgu.BUSAN_NAMGU_OWNER_BOUNDARY_AUDIT
    assert audit[namgu.BUSAN_NAMGU_PROVIDER]["registered_url"] == (
        "https://www.bsnamgu.go.kr/edu/sub03/sub06_list.php"
    )
    assert audit[namgu.BUSAN_NAMGU_PROVIDER]["canonical_url"] == (
        "https://www.bsnamgu.go.kr/edu/sub/sub.php?menucd=A0005"
    )
    assert audit[namgu.BUSAN_CITY_NAMGU_PROVIDER]["filter"] == {
        "srchGugun": "4",
        "srchResveInsttCd": "33",
    }
    assert audit[namgu.BUSAN_LIFELONG_PROVIDER]["office_code"] == "OFFICE_00002634"
    office = namgu._platform_office()
    assert office.ownership == "duplicate_dedicated_namgu_owner"
    assert office.municipality_code == ""
    assert office.municipality_name == ""
    discovery = namgu.BUSAN_NAMGU_DISCOVERY_AUDIT
    assert discovery["canonical_rows"] == 1153
    assert discovery["lifelong_rows"] == 153
    assert discovery["lifelong_native_rows"] == 152
    assert discovery["resident_rows"] == 42
    assert discovery["atomic_current_rows"] == 103
    assert discovery["canonical_application_anomaly_rows"] == 9
    assert discovery["atomic_status_counts"] == {"OPEN": 45, "CLOSED": 58}
    assert discovery["complete_network_requests"] == 248


def test_target_urls_and_shared_platform_identity_are_exact() -> None:
    assert namgu.is_busan_namgu_education_target(_target())
    assert namgu.is_busan_namgu_education_target(
        _target(url=namgu.BUSAN_NAMGU_REGISTERED_URL)
    )
    assert not namgu.is_busan_namgu_education_target(
        _target(url=namgu.BUSAN_NAMGU_CANONICAL_URL + "&se1=강좌")
    )
    assert namgu.busan_namgu_list_url(2).endswith("?menucd=A0005&pn=2")
    assert namgu.busan_namgu_city_list_url(3).endswith(
        "curPage=3&srchGugun=4&srchResveInsttCd=33"
    )
    platform_query = parse_qs(
        urlparse(namgu.busan_namgu_lifelong_list_url(1)).query
    )
    assert platform_query["pageUnit"] == ["100"]
    detail = namgu.busan_namgu_detail_url("9001")
    alias = detail + "&pn=1&se1=&se2=&se3=&se4=&se5=&key=&key_name="
    assert namgu.canonical_busan_namgu_course_identity(detail) == "idx:9001"
    assert namgu.canonical_busan_namgu_course_identity(alias) == "idx:9001"
    assert namgu.canonical_busan_namgu_course_identity(alias + "&x=1") == ""
    with pytest.raises(namgu.BusanNamguContractError):
        namgu.busan_namgu_list_url(True)
    with pytest.raises(namgu.BusanNamguContractError):
        namgu.busan_namgu_city_detail_url("157", "https://evil.example")


def test_only_exact_audited_historical_date_anomalies_are_accepted() -> None:
    assert namgu._local_iso_range(
        "2024-05-13 ~ 2024-04-11",
        identity="2574",
        field="education",
    ) == ("", "", True)
    assert namgu._local_iso_range(
        "2024-05-27 ~ 2024-05-26",
        identity="2688",
        field="application",
    ) == ("", "", True)
    assert namgu._local_iso_range(
        "2026-01-06 ~ 0000-00-00",
        identity="3057",
        field="application",
    ) == ("", "", True)
    with pytest.raises(namgu.BusanNamguContractError):
        namgu._local_iso_range(
            "2024-05-13 ~ 2024-04-11",
            identity="9999",
            field="education",
        )
    with pytest.raises(namgu.BusanNamguContractError):
        namgu._local_iso_range(
            "2099-07-01 ~ 0000-00-00",
            identity="9999",
            field="application",
        )


def test_exact_audited_city_undated_title_attribute_exception_is_closed() -> None:
    values = (
        ("기관", "남구 대연3동 주민자치회"),
        ("대상", "제한없음"),
        ("장소", "대연3동 프로그램실"),
        ("일자", "[신청] ~ [행사] ~"),
        ("방법", "-"),
        ("문의", "SECRET_UNDATED_PHONE 051-607-7777"),
    )
    definitions = "".join(
        f"<dt>{label}</dt><dd>{value}</dd>" for label, value in values
    )
    html = f"""
      <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head><body>
        <form id="srchForm" name="srchForm" method="get" action="/lctre">
          <input name="curPage" value="1">
          <select name="srchGugun"><option value="4" selected>남구</option></select>
          <select name="srchResveInsttCd"><option value="33" selected>주민자치회</option></select>
        </form>
        <ul class="reserveList"><li><a class="reserveItem"
          onclick="fn_viewProgrm('199', '23279');return false;">
          <div class="infoBox"><p class="tit" title="꽃을 그리는 시간">
            [권역]꽃을 그리는 시간</p><span class="statusMark">접수마감</span>
            <dl>{definitions}</dl></div></a></li></ul>
        <div class="paginate"><a class="pgEnd"
          href="?curPage=1&amp;srchGugun=4&amp;srchResveInsttCd=33">끝</a></div>
      </body></html>
    """
    rows, last = namgu._parse_city_page(BeautifulSoup(html, "lxml"), page=1)
    assert last == 1
    assert rows[0]["status"] == "CLOSED"
    assert rows[0]["end_date"] == ""
    assert rows[0]["raw_fields"]["audited_undated_closed_row"] is True
    with pytest.raises(namgu.BusanNamguContractError):
        namgu._parse_city_page(
            BeautifulSoup(
                html.replace('title="꽃을 그리는 시간"', 'title="변경"'),
                "lxml",
            ),
            page=1,
        )


def test_complete_three_ledger_snapshot_duplicate_suppression_and_privacy() -> None:
    backend = _Backend()
    rows, parser, meta = _collect(backend)

    assert parser == namgu.BUSAN_NAMGU_PARSER
    assert [row["provider_course_id"] for row in rows] == [
        f"{namgu.BUSAN_NAMGU_PROVIDER}:district:9001",
        f"{namgu.BUSAN_NAMGU_PROVIDER}:lifelong:LEARNING_00090001",
        f"{namgu.BUSAN_NAMGU_PROVIDER}:reserve:157:24935",
    ]
    assert meta["local_source_rows"] == 2
    assert meta["platform_source_rows"] == 2
    assert meta["city_source_rows"] == 1
    assert meta["source_total"] == 5
    assert meta["unique_education_source_rows"] == 4
    assert meta["local_current_count"] == 1
    assert meta["platform_native_rows"] == 1
    assert meta["platform_native_current_count"] == 1
    assert meta["platform_external_owner_identity_rows"] == 1
    assert meta["platform_external_visible_duplicate_rows"] == 1
    assert meta["platform_external_unpublished_test_rows"] == 0
    assert meta["city_current_count"] == 1
    assert meta["current_source_count"] == 3
    assert meta["expired_count"] == 1
    assert meta["required_list_requests"] == 12
    assert meta["list_requests"] == 12
    assert meta["sentinel_requests"] == 3
    assert meta["stability_rechecks"] == 6
    assert meta["detail_pages"] == 3
    assert meta["network_requests"] == 15
    assert meta["application_control_count"] == 3
    assert meta["status_counts"] == {"OPEN": 3}
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert {row["raw_fields"]["source_catalog"] for row in rows} == {
        "busan_namgu_a0005_general_courses",
        "busan_lifelong_namgu_native",
        "busan_reserve_namgu_resident_councils",
    }
    assert {row["branch"] for row in rows} == {
        "남구평생학습관",
        "남구청",
        "남구 대연4동 주민자치회",
    }
    assert all(row["municipality_code"] == "2629000000" for row in rows)
    assert all(row["status"] == "OPEN" for row in rows)
    assert all(row["reservation_available"] is True for row in rows)
    assert rows[0]["capacity_current"] == 3
    platform = next(row for row in rows if ":lifelong:" in row["provider_course_id"])
    assert platform["raw_fields"]["workplace_eligibility_value_never_read"] is True
    assert platform["raw_fields"]["optional_free_form_values_never_read"] is True
    assert not any("sort1=apply" in url for url in backend.urls)

    serialized = repr(rows)
    for secret in (
        "SECRET_LOCAL_INSTRUCTOR",
        "SECRET_LOCAL_PHONE",
        "SECRET_LOCAL_EMAIL",
        "SECRET_LOCAL_FAX",
        "SECRET_LOCAL_DESCRIPTION",
        "SECRET_LOCAL_IMAGE",
        "SECRET_LOCAL_NOTICE",
        "SECRET_LOCAL_PLAN",
        "SECRET_LOCAL_APPLICATION",
        "SECRET_LOCAL_OTHER",
        "SECRET_LOCAL_FREE_FORM",
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
        "SECRET_WORKPLACE_ELIGIBILITY",
        "SECRET_CITY_CARD_PHONE",
        "SECRET_CITY_DETAIL_PHONE",
        "SECRET_CITY_ATTACHMENT",
        "SECRET_CITY_FREE_FORM",
        "private@example.test",
        "city-private@example.test",
    ):
        assert secret not in serialized
    assert all(row["raw_fields"]["application_form_fetched"] is False for row in rows)


def test_waitlist_is_identity_bound_without_fetching_application_form() -> None:
    backend = _Backend(waitlist=True)
    rows, _parser, meta = _collect(backend)
    platform = next(row for row in rows if ":lifelong:" in row["provider_course_id"])
    assert meta["snapshot_complete"] is True
    assert platform["status"] == "OPEN"
    assert platform["application_type"] == "WAITLIST_APPLY"
    assert platform["reservation_available"] is True
    assert platform["application_url"] == namgu.busan_namgu_lifelong_detail_url(
        "LEARNING_00090001"
    )
    assert platform["raw_fields"]["detail_application_control_label"] == "대기자신청"
    assert not any("apply" in urlparse(url).path.casefold() for url in backend.urls)


def test_priority_application_control_is_exact_and_identity_bound() -> None:
    rows, _parser, meta = _collect(_Backend(priority=True))
    platform = next(row for row in rows if ":lifelong:" in row["provider_course_id"])
    assert meta["snapshot_complete"] is True
    assert platform["application_type"] == "ONLINE_RESERVATION"
    assert platform["reservation_available"] is True
    assert platform["raw_fields"]["detail_application_control_label"] == (
        "우선모집신청"
    )


def test_transient_status_200_error_page_is_retried_atomically() -> None:
    rows, _parser, meta = _collect(_Backend(transient_local_detail=True))
    assert len(rows) == 3
    assert meta["snapshot_complete"] is True
    assert meta["network_retry_count"] == 1
    assert meta["network_requests"] == 16


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
def test_any_source_contract_failure_discards_all_three_ledgers(flag: str) -> None:
    rows, _parser, meta = _collect(_Backend(**{flag: True}))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"]


def test_caps_dedupe_and_wrong_target_fail_closed() -> None:
    rows, _parser, meta = _collect(_Backend(), max_pages=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(_Backend(), detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(_Backend(), max_requests=11)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_requests cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(
        _Backend(), dedupe_rows=lambda values: values[:2]
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]

    backend = _Backend()
    rows, _parser, meta = namgu.collect_busan_namgu_education(
        _target(url=namgu.BUSAN_CITY_NAMGU_URL),
        fetcher=backend.fetch,
        session_factory=backend.session,
    )
    assert rows == []
    assert backend.urls == []
    assert "exact registered/canonical" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_MUNICIPAL_AUDIT") != "1",
    reason="set RUN_LIVE_MUNICIPAL_AUDIT=1 for the 248-request live audit",
)
def test_live_complete_snapshot_matches_latest_audit_floor() -> None:
    rows, _parser, meta = namgu.collect_busan_namgu_education(
        _target(), today="2026-07-22"
    )
    assert meta["snapshot_complete"] is True
    assert meta["local_source_rows"] >= 1153
    assert meta["local_data_pages"] >= 129
    assert meta["local_current_count"] >= 0
    assert meta["platform_source_rows"] >= 153
    assert meta["platform_native_rows"] >= 152
    assert meta["platform_native_current_count"] >= 62
    assert meta["city_source_rows"] >= 42
    assert meta["city_current_count"] >= 41
    assert len(rows) == meta["current_source_count"]
