from __future__ import annotations

from html import escape
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_busan_saha as saha


class _Response:
    def __init__(self, url: str, html: str, status: int = 200):
        self.url = url
        self.content = html.encode("utf-8")
        self.status_code = status
        self.history: list[Any] = []


class _Session:
    def close(self) -> None:
        return None


def _target(url: str = saha.BUSAN_SAHA_URL, provider: str = saha.BUSAN_SAHA_PROVIDER):
    return {"provider": provider, "url": url}


def _local_row(
    *,
    page: int,
    seq: str,
    title: str,
    start: str,
    end: str,
    apply_start: str,
    apply_end: str,
    status: str,
) -> str:
    return f"""
      <tr><td class="class_name"><a class="className" href="javascript:void(0)"
        onclick="fn_view_page('{page}', '{seq}')" title="{escape(title)}"><dl>
        <dt>{escape(title)}</dt><dd>접수기간: {apply_start}~{apply_end}</dd>
        <dd>학습기간: {start}~{end}</dd><dd>교육장소: 사하구평생학습관</dd>
      </dl></a></td><td><span class="state">{status}</span></td>
      <td>20</td><td>SECRET_ENROLLMENT</td><td>0</td><td>성인</td>
      <td><span>온라인접수</span></td></tr>
    """.replace("SECRET_ENROLLMENT", "3")


def _local_page(page: int, *, drift: bool = False, bad_sentinel: bool = False) -> str:
    if page == 1:
        rows = _local_row(
            page=1,
            seq="2",
            title="변경된 현재 강좌" if drift else "현재 강좌",
            start="2099-08-01",
            end="2099-08-31",
            apply_start="2099-07-01",
            apply_end="2099-07-31",
            status="접수중",
        )
        rows += _local_row(
            page=1,
            seq="1",
            title="과거 강좌",
            start="2020-08-01",
            end="2020-08-31",
            apply_start="2020-07-01",
            apply_end="2020-07-31",
            status="종강",
        )
        pager = '<div class="box_page"><a class="btn_end" onclick="goPage(1); return false;">끝</a></div>'
    elif bad_sentinel:
        rows = _local_row(
            page=page,
            seq="3",
            title="경계 이탈",
            start="2099-09-01",
            end="2099-09-30",
            apply_start="2099-08-01",
            apply_end="2099-08-31",
            status="접수중",
        )
        pager = '<div class="box_page"><a class="btn_end" onclick="goPage(1); return false;">끝</a></div>'
    else:
        rows = '<tr><td colspan="7"><span>등록된 강좌가 없습니다.</span></td></tr>'
        pager = ""
    return f"""
      <html><head><title>강좌목록&amp;신청 | 부산광역시 사하구</title></head><body>
        <form id="searchForm" name="searchForm" action="{saha.BUSAN_SAHA_PATH}?mId={saha.BUSAN_SAHA_MID}">
          <input name="page" value="{page}"><input name="seq" value="0">
          <input name="lecType" value="1">
        </form>
        <div class="board_edu_page">페이지 : {page} / 2 &nbsp; 전체게시물 : 2</div>
        <table class="tableSt_list"><thead><tr>{''.join(f'<th>{x}</th>' for x in saha._LOCAL_HEADERS)}</tr></thead>
          <tbody>{rows}</tbody></table>{pager}
      </body></html>
    """


def _local_detail(*, wrong_identity: bool = False) -> str:
    values = {
        "강좌구분": "사하구평생학습관",
        "과정분류": "인문교양",
        "지역": "사하구",
        "학습기관": "평생학습관",
        "학습기간": "2099.08.01 ~ 2099.08.31",
        "접수기간": "2099.07.01 ~ 2099.07.31",
        "강사명": "SECRET_LOCAL_INSTRUCTOR 010-1111-2222",
        "수강료": "무료",
        "교육방법": "오프라인",
        "교육대상": "성인",
        "교육주기": "수 10:00 ~ 12:00",
        "교육정원": "20명",
        "문의전화": "SECRET_LOCAL_PHONE 051-220-0000",
        "접수방법": "온라인접수",
        "교육장소": "사하구평생학습관",
        "URL": "",
        "상세내용": "SECRET_LOCAL_DESCRIPTION private@example.test",
        "강의계획서": "SECRET_LOCAL_ATTACHMENT.pdf",
    }
    rows = "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(values[label])}</td></tr>"
        for label in (
            "강좌구분", "과정분류", "지역", "학습기관", "학습기간", "접수기간",
            "강사명", "수강료", "교육방법", "교육대상", "교육주기", "교육정원",
            "문의전화", "접수방법", "교육장소", "URL", "상세내용", "강의계획서",
        )
    )
    seq = "999" if wrong_identity else "2"
    return f"""
      <html><head><title>강좌목록&amp;신청 | 부산광역시 사하구</title></head><body>
        <form id="listForm" name="listForm"><input name="page" value="1"><input name="seq" value="{seq}"></form>
        <table class="table_view"><tbody><tr><th class="title">현재 강좌
          <span><span class="state">접수중</span></span></th></tr>{rows}</tbody></table>
        <div class="btn_area"><a class="btn" href="javascript:fn_lec_receipt('2');">접수하기</a></div>
      </body></html>
    """


def _platform_row(
    sequence: int,
    *,
    office_name: str,
    identity: str,
    title: str,
    start: str,
    end: str,
    status: str,
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
      <tr><td>{sequence}</td><td class="subject"><a {title_action}>
        <span class="tit">{escape(title)}</span><span class="org">{escape(office_name)}</span></a></td>
        <td><span>무료</span></td><td><span class="s_type blue"><em class="hidden">교육기간</em>
        {start}~{end}<pre>수, 10:00~12:00</pre></span></td>
        <td><span class="s_type indigo1"><em class="hidden">모집인원</em>20명</span>
        <span class="s_type red1"><em class="hidden">일반접수</em>2099.07.01~2099.07.31</span></td>
        <td><span class="s_type2"><em class="hidden">선착순</em></span><span class="s_btn">{status}</span></td>
        <td>{action}</td></tr>
    """


def _platform_page(
    office_code: str,
    page: int,
    *,
    unmatched_external: bool = False,
    drift: bool = False,
) -> str:
    office_name = dict(saha.BUSAN_LIFELONG_SAHA_OFFICES)[office_code]
    body = ""
    if page == 1 and office_code == "OFFICE_00002632":
        external_seq = "999" if unmatched_external else "1"
        external = saha.busan_saha_detail_url(external_seq)
        body = _platform_row(
            2,
            office_name=office_name,
            identity="LEARNING_00090001",
            title="변경된 공유 강좌" if drift else "공유 강좌",
            start="2099.08.01",
            end="2099.08.31",
            status="접수중",
        )
        body += _platform_row(
            1,
            office_name=office_name,
            identity=external,
            title="과거 강좌",
            start="2020.08.01",
            end="2020.08.31",
            status="교육완료",
            external_url=external,
        )
    else:
        body = '<tr><td colspan="7">등록된 교육강좌가 없습니다.</td></tr>'
    return f"""
      <html><head><title>부산평생학습플랫폼</title></head><body>
        <form id="learningVO" method="post" action="{saha.BUSAN_LIFELONG_LIST_PATH}">
          <input name="inst_id" value="{office_code}"><input name="display_type" value="2">
          <input name="pageIndex" value="{page}"><input name="l_search_ch" value="0">
          <select id="o_search_ch"><option value="{office_code}" selected>{escape(office_name)}</option></select>
          <select id="learning_state"><option value="0" selected>전체</option></select>
        </form><table><thead><tr><th>번호</th><th>강좌명 / 교육기관</th>
          <th>재료비 / 강사</th><th>교육기간 / 교육시간</th>
          <th>신청기간 / 접수인원 / 대기자</th><th>상태</th><th>보기</th>
        </tr></thead><tbody>{body}</tbody></table>
        <a class="page_nextend" href="?pageIndex=1" onclick="fn_list(1,'');return false;">마지막</a>
      </body></html>
    """


def _platform_detail(*, wrong_title: bool = False) -> str:
    safe = {
        "강좌분류": "인문교양", "교육대상": "부산시민", "교육장소": "사하구평생학습관",
        "총 교육시간": "8시간", "교육기간": "2099.08.01 ~ 2099.08.31",
        "교육시간": "수 10:00 ~ 12:00", "수강료": "무료", "재료비": "무료",
        "우선모집기간": "해당없음", "일반모집기간": "2099.07.01 ~ 2099.07.31",
        "모집방법": "선착순", "신청상태": "일반 접수중", "교육상태": "교육예정", "결제방법": "무료",
    }
    unsafe = {
        "회차명": "SECRET_SESSION", "문의전화": "SECRET_PHONE 051-220-0000",
        "접수인원": "SECRET_ENROLLMENT", "강좌소개": "SECRET_DESCRIPTION private@example.test",
        "강좌소개 첨부파일": "SECRET_ATTACHMENT", "강사": "SECRET_INSTRUCTOR 010-1111-2222",
        "강의계획서": "SECRET_PLAN", "주의사항": "SECRET_WARNING", "검색키워드": "SECRET_KEYWORD",
        "강좌제한": "SECRET_LIMIT",
    }
    defs = "".join(
        f"<dl><dt>{label}</dt><dd>{escape(safe.get(label, unsafe.get(label, '')))}</dd></dl>"
        for label in saha._PLATFORM_REQUIRED
    )
    title = "다른 공유 강좌" if wrong_title else "공유 강좌"
    return f"""
      <html><head><title>부산평생학습플랫폼</title></head><body>
        <form><input name="inst_id" value="OFFICE_00002632"><input name="lng_id" value="LEARNING_00090001"></form>
        <h2 class="enrolTit"><span>[사하구청]</span>{title}</h2>
        <div class="form_group">{defs}</div>
        <a id="learning_aply_btn" onclick="fn_learning_apply(); return false;">일반모집신청</a>
      </body></html>
    """


def _city_card() -> str:
    values = (
        ("기관", "사하구 괴정1동 주민자치회"), ("대상", "제한없음"),
        ("장소", "프로그램실"),
        ("일자", "[신청] 2099-07-01 ~ 2099-07-31 [행사] 2099-08-01 ~ 2099-08-31"),
        ("방법", "온라인(선착순)"), ("문의", "SECRET_CARD_PHONE 051-220-0000"),
    )
    defs = "".join(f"<dt>{label}</dt><dd>{escape(value)}</dd>" for label, value in values)
    return f"""
      <li><a class="reserveItem" onclick="fn_viewProgrm('190', '10001');return false;">
        <div class="infoBox"><p class="tit" title="주민센터 요가">주민센터 요가</p>
        <span class="statusMark">접수중</span><dl>{defs}</dl></div></a></li>
    """


def _city_page(page: int, *, bad_sentinel: bool = False) -> str:
    cards = _city_card() if page == 1 or bad_sentinel else ""
    root = f'<ul class="reserveList">{cards}</ul>' if cards else ""
    return f"""
      <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head><body>
        <form id="srchForm" name="srchForm" method="get" action="/lctre">
          <input name="curPage" value="{page}"><select name="srchGugun"><option value="10" selected>사하구</option></select>
          <select name="srchResveInsttCd"><option value="33" selected>주민자치회</option></select>
        </form>{root}<div class="paginate"><a class="pgEnd"
          href="?curPage=1&amp;srchGugun=10&amp;srchResveInsttCd=33">마지막</a></div>
      </body></html>
    """


def _city_detail(*, wrong_identity: bool = False) -> str:
    values = (
        ("운영기간", "2099-08-01 ~ 2099-08-31"), ("신청기간", "2099-07-01 ~ 2099-07-31"),
        ("취소여부", "취소 가능"), ("신청방법", "온라인(선착순)"), ("수강료", "무료"),
        ("요일 /시간", "수 / 10:00 ~ 12:00"), ("문의전화", "SECRET_CITY_PHONE 051-220-0000"),
        ("운영기관", "사하구 괴정1동 주민자치회"), ("대상", "제한없음"),
        ("첨부파일", "SECRET_CITY_ATTACHMENT"),
    )
    defs = "".join(f"<dl><dt>{label}</dt><dd>{escape(value)}</dd></dl>" for label, value in values)
    program = "999" if wrong_identity else "10001"
    return f"""
      <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head><body>
        <form id="viewForm" method="post"><input name="resveGroupSn" value="190">
          <input name="progrmSn" value="{program}"><div class="contHeader"><h3 class="titPage">주민센터 요가
          <span class="statusMark">접수중</span></h3></div>
          <div class="reserveStateWrap"><div class="reserveStateInfo">{defs}</div>
          <div class="reserveBtnWrap"><a class="btnTypeXL">예약하기</a></div></div>
          <div class="reserveDetail">SECRET_FREE_FORM private@example.test</div>
        </form>
      </body></html>
    """


class _Backend:
    def __init__(self, **flags: Any):
        self.flags = flags
        self.urls: list[str] = []
        self.calls: dict[str, int] = {}

    def session_factory(self) -> _Session:
        return _Session()

    def fetcher(self, _session: Any, url: str, _timeout: int) -> _Response:
        self.urls.append(url)
        self.calls[url] = self.calls.get(url, 0) + 1
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.hostname == saha.BUSAN_SAHA_HOST and parsed.path == saha.BUSAN_SAHA_PATH:
            page = int(query["page"][0])
            drift = bool(self.flags.get("local_drift") and page == 1 and self.calls[url] > 1)
            return _Response(url, _local_page(page, drift=drift, bad_sentinel=bool(self.flags.get("local_bad_sentinel") and page == 2)))
        if parsed.hostname == saha.BUSAN_SAHA_HOST and parsed.path == saha.BUSAN_SAHA_DETAIL_PATH:
            return _Response(url, _local_detail(wrong_identity=bool(self.flags.get("local_wrong_detail"))))
        if parsed.hostname == saha._lifelong.BUSAN_LIFELONG_HOST and parsed.path == saha.BUSAN_LIFELONG_LIST_PATH:
            office = query["inst_id"][0]
            page = int(query["pageIndex"][0])
            drift = bool(self.flags.get("platform_drift") and page == 1 and self.calls[url] > 1)
            return _Response(url, _platform_page(office, page, unmatched_external=bool(self.flags.get("unmatched_external")), drift=drift))
        if parsed.hostname == saha._lifelong.BUSAN_LIFELONG_HOST and parsed.path == saha.BUSAN_LIFELONG_DETAIL_PATH:
            return _Response(url, _platform_detail(wrong_title=bool(self.flags.get("platform_wrong_detail"))))
        if parsed.hostname == saha.BUSAN_CITY_HOST and parsed.path == saha.BUSAN_CITY_LIST_PATH:
            page = int(query["curPage"][0])
            return _Response(url, _city_page(page, bad_sentinel=bool(self.flags.get("city_bad_sentinel") and page == 2)))
        if parsed.hostname == saha.BUSAN_CITY_HOST and parsed.path == saha.BUSAN_CITY_DETAIL_PATH:
            return _Response(url, _city_detail(wrong_identity=bool(self.flags.get("city_wrong_detail"))))
        raise AssertionError(f"unexpected fetch {url}")


def _collect(backend: _Backend, **kwargs: Any):
    return saha.collect_busan_saha_education(
        _target(),
        today="2099-07-22",
        fetcher=backend.fetcher,
        session_factory=backend.session_factory,
        sleeper=lambda _seconds: None,
        max_workers=4,
        **kwargs,
    )


def test_target_identity_and_owner_boundary_are_exact() -> None:
    assert saha.is_busan_saha_education_target(_target())
    assert saha.is_busan_saha_education_target(_target(saha.BUSAN_SAHA_REGISTERED_URL))
    assert not saha.is_busan_saha_education_target(_target(saha.BUSAN_SAHA_RESERVATION_ALIAS_URL))
    assert not saha.is_busan_saha_education_target(_target(provider="WRONG"))
    assert saha.canonical_busan_saha_course_identity(
        "http://www.saha.go.kr/edu/lecture/view.do?mId=0201010000&seq=5947"
    ) == "seq:5947"
    assert saha.canonical_busan_saha_course_identity(
        "http://www.saha.go.kr/other/view.do?mId=0201010000&seq=5947"
    ) == ""
    assert tuple(saha.BUSAN_SAHA_DISCOVERY_AUDIT["platform_rows_by_office"]) == (
        "OFFICE_00002632", "OFFICE_00002790"
    )


def test_complete_atomic_snapshot_suppresses_exact_duplicate_and_pii() -> None:
    backend = _Backend()
    rows, parser, meta = _collect(backend)
    assert parser == saha.BUSAN_SAHA_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["local_source_rows"] == 2
    assert meta["platform_source_rows"] == 2
    assert meta["platform_external_duplicate_rows"] == 1
    assert meta["platform_native_rows"] == 1
    assert meta["city_source_rows"] == 1
    assert meta["source_total"] == 5
    assert meta["unique_education_source_rows"] == 4
    assert meta["current_source_count"] == 3
    assert meta["required_list_requests"] == 14
    assert meta["required_detail_requests"] == 3
    assert meta["network_requests"] == 17
    assert len(rows) == 3
    assert meta["status_counts"] == {"OPEN": 3}
    assert meta["application_control_count"] == 3
    assert all(row["municipality_code"] == "2638000000" for row in rows)
    assert all(row["raw_fields"]["detail_verified"] is True for row in rows)
    assert not any(":local:1" in row["provider_course_id"] for row in rows)
    rendered = repr(rows)
    assert "SECRET_" not in rendered
    assert "private@example.test" not in rendered
    assert "010-" not in rendered and "051-" not in rendered


@pytest.mark.parametrize(
    "flag, message",
    [
        ("unmatched_external", "does not match canonical"),
        ("local_bad_sentinel", "sentinel"),
        ("local_drift", "boundary page changed"),
        ("platform_drift", "complete census changed"),
        ("local_wrong_detail", "hidden identity"),
        ("platform_wrong_detail", "title mismatch"),
        ("city_wrong_detail", "detail identity"),
    ],
)
def test_any_contract_failure_discards_the_whole_snapshot(flag: str, message: str) -> None:
    rows, _parser, meta = _collect(_Backend(**{flag: True}))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert message in meta["configured_collection_error"]


def test_caps_and_dedupe_fail_closed() -> None:
    rows, _parser, meta = _collect(_Backend(), detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(_Backend(), dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]


def test_wrong_target_never_touches_network() -> None:
    backend = _Backend()
    rows, _parser, meta = saha.collect_busan_saha_education(
        _target(url=saha.BUSAN_SAHA_URL + "&other=1"),
        fetcher=backend.fetcher,
        session_factory=backend.session_factory,
    )
    assert rows == []
    assert backend.urls == []
    assert "exact canonical" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_MUNICIPAL_AUDIT") != "1",
    reason="set RUN_LIVE_MUNICIPAL_AUDIT=1 for the 124-request Saha audit",
)
def test_live_complete_snapshot_matches_exact_2026_07_22_audit() -> None:
    rows, _parser, meta = saha.collect_busan_saha_education(
        _target(), today="2026-07-22"
    )
    assert meta["snapshot_complete"] is True
    assert meta["local_source_rows"] == 950
    assert meta["local_data_pages"] == 95
    assert meta["local_current_count"] == 0
    assert meta["local_source_status_counts"] == {"종강": 583, "접수마감": 367}
    assert meta["platform_rows_by_office"] == {
        "OFFICE_00002632": 73,
        "OFFICE_00002790": 0,
    }
    assert meta["platform_external_duplicate_rows"] == 10
    assert meta["platform_native_rows"] == 63
    assert meta["platform_native_current_count"] == 15
    assert meta["city_source_rows"] == 0
    assert meta["source_total"] == 1023
    assert meta["unique_education_source_rows"] == 1013
    assert len(rows) == 15
    assert meta["status_counts"] == {"OPEN": 3, "CLOSED": 12}
    assert meta["application_control_count"] == 3
    assert meta["required_list_requests"] == 109
    assert meta["required_detail_requests"] == 15
    assert meta["network_requests"] == 124
