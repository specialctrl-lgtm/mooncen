from __future__ import annotations

from collections import Counter
from html import escape
import os
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_busan_busanjin as busanjin


class _Response:
    def __init__(self, url: str, html: str, status_code: int = 200):
        self.url = url
        self.content = html.encode("utf-8")
        self.status_code = status_code
        self.history: list[Any] = []


class _Session:
    def close(self) -> None:
        pass


def _target(
    provider: str = busanjin.BUSAN_BUSANJIN_PROVIDER,
    url: str = busanjin.BUSAN_BUSANJIN_CANONICAL_URL,
) -> dict[str, str]:
    return {"provider": provider, "url": url, "name": "부산진구 교육"}


_LOCAL_ROWS = (
    {
        "identity": "9001",
        "lecture_code": "20990101ABCD0001",
        "title": "미래 시민교실",
        "start": "2099-08-01",
        "end": "2099-08-31",
        "status": "접수중",
    },
    {
        "identity": "9000",
        "lecture_code": "20980101ABCD0002",
        "title": "지난 시민교실",
        "start": "2098-08-01",
        "end": "2098-08-31",
        "status": "접수마감",
    },
)


def _local_detail_form(row: dict[str, str], page: int) -> str:
    return f"""
      <form id="goEduDetail{row['identity']}" method="post"
        action="{busanjin.BUSAN_BUSANJIN_DETAIL_PATH}">
        <input name="pageIndex" value="{page}">
        <input name="pageUnit" value="6"><input name="pageSize" value="5">
        <input name="idx" value="{row['identity']}">
        <input name="lectureCode" value="{row['lecture_code']}">
        <input name="lectureSort" value="평생학습관강좌">
        <input name="menuCd" value="{busanjin.BUSAN_BUSANJIN_MENU}">
        <input name="orgLectGubun" value="">
        <input name="period" value="1"><input name="periodLimit" value="Y">
        <input name="limitNum" value="20">
      </form>
    """


def _local_card(row: dict[str, str]) -> str:
    status_class = "Receipt" if row["status"] == "접수중" else "Accept"
    fields = (
        ("접수기간", "2099-07-01 ~ 2099-07-31"),
        ("대기신청", "2099-07-01 ~ 2099-07-31"),
        ("강의기간", f"{row['start']} ~ {row['end']}"),
        ("대상", "부산진구민"),
        ("신청/정원(전체정원)", "3명/20명 (20명)"),
        ("온라인대기", "0명/5명"),
        ("접수방법", "인터넷"),
    )
    values = "".join(
        f"<li>{escape(label)} : {escape(value)}</li>" for label, value in fields
    )
    return f"""
      <li><a class="bd" onclick="goDetail({row['identity']}, this)">
        <span><i class="{status_class}">{row['status']}</i></span>
        <span class="edu-tit">인문교양</span>
        <span class="course-tit">{escape(row['title'])}</span>
        <ul class="cont-list">{values}</ul>
      </a></li>
    """


def _local_page(
    page: int, *, drift: bool = False, bad_sentinel: bool = False
) -> str:
    has_rows = page == 1 or bad_sentinel
    rows = [dict(row) for row in _LOCAL_ROWS]
    if drift:
        rows[0]["title"] = "변경된 미래 시민교실"
    forms = "".join(_local_detail_form(row, page) for row in rows) if has_rows else ""
    cards = "".join(_local_card(row) for row in rows) if has_rows else ""
    board = f'<div class="board-list"><ul class="gallery01 li-wt">{cards}</ul></div>' if has_rows else ""
    return f"""
      <html><body><div class="guide-wrap">
        <div class="total">총 게시물 : <strong>2</strong>건, 페이지 :
          <strong>{page}</strong>/<strong>1</strong></div>
        <form name="searchForm" method="post"
          action="{busanjin.BUSAN_BUSANJIN_LIST_PATH}">
          <input name="pageIndex" value="{page}">
          <input name="pageUnit" value="6"><input name="pageSize" value="5">
          <input name="menuCd" value="{busanjin.BUSAN_BUSANJIN_MENU}">
          <input name="orgLectGubun" value=""><input name="allYn" value="Y">
          <select name="eduRegion"><option value="" selected>전체</option></select>
          <input name="title" value="">
        </form>
        {forms}{board}
      </div></body></html>
    """


def _local_detail(*, wrong_title: bool = False) -> str:
    title = "다른 시민교실" if wrong_title else "미래 시민교실"
    values = {
        "접수기간": "2099-07-01 ~ 2099-07-31",
        "대기신청": "2099-07-01 ~ 2099-07-31",
        "신청/정원": "3명/20명",
        "전체정원": "20명",
        "강좌기간": "2099-08-01 ~ 2099-08-31",
        "강좌시간": "수 14:00~16:00",
        "강의실": "평생학습관 1강의실",
        "주최": "부산진구청",
    }
    items = "".join(
        (
            '<li><span class="tit">첨부파일</span>'
            '<a>SECRET_LOCAL_ATTACHMENT private@example.test</a></li>'
            if label == "첨부파일"
            else f'<li><span class="tit">{label}</span>{escape(values[label])}</li>'
        )
        for label in busanjin._LOCAL_DETAIL_LABELS
    )
    return f"""
      <html><head><title>강좌·교육 &gt; 평생학습관 | 부산 진구청</title></head>
      <body>
        <form name="goApplyForm" method="post"
          action="{busanjin.BUSAN_BUSANJIN_APPLY_PATH}">
          <input name="menuCd" value="{busanjin.BUSAN_BUSANJIN_MENU}">
          <input name="idx" value="9001">
          <input name="lectureCode" value="20990101ABCD0001">
          <input name="lectureName" value="미래 시민교실">
          <input name="lectureSort" value="평생학습관강좌">
          <input name="period" value="1">
          <input name="csrf" value="SECRET_CSRF_VALUE">
        </form>
        <ul class="edu-dt-listbox"><li>
          <h5 class="list-tit">{escape(title)}</h5>
          <p class="label-Receipt"><span>접수중</span></p>
          <ul class="edu-dt-listtype01">{items}</ul>
          <div class="btn-wrap" data-state2="2" data-test2="2">
            <a onclick="goApplyPage(2)">신청하기</a>
          </div>
        </li></ul>
        <div class="dt-conbox-infor">SECRET_LOCAL_FREE_FORM 010-1111-2222</div>
        <div class="dt-conbox-list">SECRET_APPLICANT_TABLE private@example.test</div>
      </body></html>
    """


def _platform_row(sequence: int, row: dict[str, str]) -> str:
    external = busanjin.busan_busanjin_detail_url(
        row["identity"], row["lecture_code"]
    )
    return f"""
      <tr>
        <td>{sequence}</td>
        <td class="subject"><a href="{escape(external, quote=True)}" target="_blank">
          <span class="tit">{escape(row['title'])}</span>
          <span class="org">부산진구청</span>
        </a></td>
        <td class="type"><span>무료</span><br>
          <span>SECRET_PLATFORM_INSTRUCTOR 010-2222-3333</span></td>
        <td><span class="s_type blue"><em class="hidden">교육기간</em>
          {row['start']}~{row['end']}<pre>수 14:00~16:00</pre></span></td>
        <td><span class="s_type indigo1"><em class="hidden">모집인원</em>20명</span>
          <span class="s_type red1"><em class="hidden">일반접수</em>
          2099-07-01~2099-07-31 ( 접수인원 : SECRET_ENROLLMENT )</span></td>
        <td><span class="s_type2 mint"><em class="hidden">선착순</em></span>
          <span class="s_btn blue">{'접수중' if row['status'] == '접수중' else '교육완료'}</span></td>
        <td><a href="{escape(external, quote=True)}"><span class="button">수강신청</span></a></td>
      </tr>
    """


def _platform_page(
    page: int,
    *,
    drift: bool = False,
    bad_sentinel: bool = False,
    unmatched_external: bool = False,
) -> str:
    body = '<tr><td colspan="7">등록된 교육강좌가 없습니다.</td></tr>'
    if page == 1 or bad_sentinel:
        rows = [dict(row) for row in _LOCAL_ROWS]
        if drift:
            rows[0]["title"] = "변경된 미래 시민교실"
        if unmatched_external:
            rows[0]["identity"] = "9999"
        body = _platform_row(2, rows[0]) + _platform_row(1, rows[1])
    return f"""
      <html><head><title>부산평생학습플랫폼</title></head><body>
        <form id="learningVO" method="post"
          action="{busanjin.BUSAN_LIFELONG_LIST_PATH}">
          <input name="inst_id" value="{busanjin.BUSAN_LIFELONG_BUSANJIN_OFFICE}">
          <input name="display_type" value="2"><input name="pageIndex" value="{page}">
          <input name="pageUnit" value="1000"><input name="l_search_ch" value="0">
          <select id="o_search_ch"><option
            value="{busanjin.BUSAN_LIFELONG_BUSANJIN_OFFICE}" selected>부산진구청</option></select>
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


def _city_card(
    *, title: str = "주민센터 생활요가", branch: str = "부산진구 부암1동 주민자치회"
) -> str:
    values = (
        ("기관", branch),
        ("대상", "제한없음"),
        ("장소", "부암1동 프로그램실"),
        ("일자", "[신청] 2099-07-01 ~ 2099-07-31 [행사] 2099-08-01 ~ 2099-08-31"),
        ("방법", "방문접수"),
        ("문의", "SECRET_CITY_CARD_PHONE 051-800-9999"),
    )
    definitions = "".join(
        f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in values
    )
    return f"""
      <li><a class="reserveItem" href="javascript:void(0);"
        onclick="fn_viewProgrm('334', '25009');return false;">
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
    reserve_list = ""
    if page == 1 or bad_sentinel:
        card = _city_card(
            title="변경된 주민센터 생활요가" if drift else "주민센터 생활요가",
            branch=(
                "서구 다른동 주민자치회"
                if wrong_owner
                else "부산진구 부암1동 주민자치회"
            ),
        )
        reserve_list = f'<ul class="reserveList">{card}</ul>'
    return f"""
      <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head><body>
        <form id="srchForm" name="srchForm" method="get" action="/lctre">
          <input name="curPage" value="{page}">
          <select name="srchGugun"><option value="7" selected>부산진구</option></select>
          <select name="srchResveInsttCd"><option value="33" selected>주민자치회</option></select>
        </form>
        {reserve_list}
        <div class="paginate"><a class="pgEnd"
          href="?curPage=1&amp;srchGugun=7&amp;srchResveInsttCd=33">마지막</a></div>
      </body></html>
    """


def _city_detail(*, wrong_identity: bool = False) -> str:
    program = "99999" if wrong_identity else "25009"
    values = (
        ("운영기간", "2099-08-01(토) ~ 2099-08-31(월)"),
        ("신청기간", "2099-07-01(수) 09:00 ~ 2099-07-31(금) 18:00"),
        ("취소여부", "취소 가능"),
        ("신청방법", "방문접수"),
        ("수강료", "0 원"),
        ("요일 /시간", "수 / 14:00 ~ 16:00"),
        ("문의전화", "SECRET_CITY_DETAIL_PHONE 051-800-8888"),
        ("운영기관", "부산진구 부암1동 주민자치회"),
        ("대상", "제한없음"),
        ("첨부파일", "SECRET_CITY_ATTACHMENT private@example.test"),
    )
    definitions = "".join(
        f"<dl><dt>{escape(label)}</dt><dd>{escape(value)}</dd></dl>"
        for label, value in values
    )
    return f"""
      <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head><body>
        <form id="viewForm" method="post">
          <input name="resveGroupSn" value="334">
          <input name="progrmSn" value="{program}">
          <div class="contHeader"><h3 class="titPage">주민센터 생활요가
            <span class="statusMark possible">접수중</span></h3></div>
          <div class="reserveStateWrap">
            <div class="reserveStateInfo">{definitions}</div>
            <div class="reserveBtnWrap"><a class="btnTypeXL" href="#">방문예약</a></div>
          </div>
          <div class="reserveDetail">SECRET_CITY_FREE_FORM city@example.test</div>
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
        self.wrong_city_owner = wrong_city_owner
        self.wrong_local_title = wrong_local_title
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
        if parsed.hostname == busanjin.BUSAN_BUSANJIN_HOST:
            if parsed.path == busanjin.BUSAN_BUSANJIN_DETAIL_PATH:
                identity = (query.get("idx") or [""])[0]
                count = self._record(f"local-detail-{identity}", url)
                if self.transient_local_detail and count == 1:
                    return _Response(url, "<html><head><title>temporary</title></head><body>retry</body></html>")
                return _Response(url, _local_detail(wrong_title=self.wrong_local_title))
            if parsed.path == busanjin.BUSAN_BUSANJIN_LIST_PATH:
                page = int((query.get("pageIndex") or ["1"])[0])
                count = self._record(f"local-list-{page}", url)
                return _Response(
                    url,
                    _local_page(
                        page,
                        drift=self.local_drift and page == 1 and count >= 2,
                        bad_sentinel=self.bad_local_sentinel,
                    ),
                )
        if parsed.hostname == "lll.busan.go.kr" and parsed.path == busanjin.BUSAN_LIFELONG_LIST_PATH:
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
        if parsed.hostname == busanjin.BUSAN_CITY_HOST:
            if parsed.path == busanjin.BUSAN_CITY_LIST_PATH:
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
            if parsed.path == busanjin.BUSAN_CITY_DETAIL_PATH:
                self._record("city-detail", url)
                return _Response(url, _city_detail(wrong_identity=self.wrong_city_identity))
        raise AssertionError(f"unexpected fetch {url}")


def _collect(backend: _Backend, **kwargs: Any):
    return busanjin.collect_busan_busanjin_education(
        _target(),
        timeout=5,
        max_pages=kwargs.pop("max_pages", 20),
        detail_limit=kwargs.pop("detail_limit", 5),
        max_requests=kwargs.pop("max_requests", 30),
        today="2099-07-20",
        fetcher=backend.fetch,
        session_factory=backend.session,
        sleeper=lambda _seconds: None,
        max_workers=1,
        **kwargs,
    )


def test_provider_candidates_owner_boundaries_and_audit_are_exact() -> None:
    assert busanjin.BUSAN_BUSANJIN_PROVIDER == "MUNI_WWW_BUSANJIN_GO_KR_5881F59A"
    assert busanjin.BUSAN_CITY_BUSANJIN_PROVIDER == "MUNI_RESERVE_BUSAN_GO_KR_D884D074"
    assert busanjin.BUSAN_LIFELONG_PROVIDER == "MUNI_LLL_BUSAN_GO_KR_944C621B"
    assert set(busanjin.BUSAN_BUSANJIN_CANDIDATE_IDS.values()) == {
        "MUNI_IR_7BBE29A9BFD4",
        "MUNI_IR_32B236CC359D",
        "MUNI_IR_4332B8F8A6D7",
        "MUNI_IR_2BA97ED12CEB",
        "MUNI_IR_5608F8475923",
    }
    audit = busanjin.BUSAN_BUSANJIN_OWNER_BOUNDARY_AUDIT
    assert audit[busanjin.BUSAN_BUSANJIN_PROVIDER]["registered_url"] == (
        "https://www.busanjin.go.kr/index.busanjin?menuCd=DOM_000000209001004000"
    )
    assert audit[busanjin.BUSAN_BUSANJIN_PROVIDER]["canonical_url"] == (
        "https://www.busanjin.go.kr/reserve/index.busanjin?menuCd=DOM_000001501001000000"
    )
    assert audit[busanjin.BUSAN_CITY_BUSANJIN_PROVIDER]["filter"] == {
        "srchGugun": "7",
        "srchResveInsttCd": "33",
    }
    assert audit[busanjin.BUSAN_LIFELONG_PROVIDER]["office_code"] == "OFFICE_00002710"
    assert (
        busanjin._platform_office().ownership
        == "duplicate_dedicated_busanjin_owner"
    )
    discovery = busanjin.BUSAN_BUSANJIN_DISCOVERY_AUDIT
    assert discovery["district_rows"] == 977
    assert discovery["resident_rows"] == 152
    assert discovery["lifelong_rows"] == 977
    assert discovery["lifelong_native_rows"] == 0
    assert discovery["atomic_current_rows"] == 252
    assert discovery["atomic_required_requests_without_retries"] == 440


def test_target_urls_and_external_identity_are_exact() -> None:
    assert busanjin.is_busan_busanjin_education_target(_target())
    assert busanjin.is_busan_busanjin_education_target(
        _target(url=busanjin.BUSAN_BUSANJIN_REGISTERED_URL)
    )
    assert not busanjin.is_busan_busanjin_education_target(
        _target(url=busanjin.BUSAN_BUSANJIN_CANONICAL_URL + "&title=수영")
    )
    assert parse_qs(urlparse(busanjin.busan_busanjin_list_url(2)).query) == {
        "menuCd": [busanjin.BUSAN_BUSANJIN_MENU],
        "allYn": ["Y"],
        "cpath": ["/reserve"],
        "pageIndex": ["2"],
        "pageUnit": ["6"],
        "pageSize": ["5"],
    }
    assert busanjin.busan_busanjin_city_list_url(3).endswith(
        "curPage=3&srchGugun=7&srchResveInsttCd=33"
    )
    platform = parse_qs(urlparse(busanjin.busan_busanjin_lifelong_list_url(1)).query)
    assert platform["inst_id"] == ["OFFICE_00002710"]
    assert platform["pageUnit"] == ["1000"]
    detail = busanjin.busan_busanjin_detail_url("9001", "20990101ABCD0001")
    assert busanjin.canonical_busan_busanjin_course_identity(detail) == "idx:9001"
    assert busanjin.canonical_busan_busanjin_course_identity(detail + "&x=1") == ""
    with pytest.raises(busanjin.BusanBusanjinContractError):
        busanjin.busan_busanjin_list_url(True)
    with pytest.raises(busanjin.BusanBusanjinContractError):
        busanjin.busan_busanjin_detail_url("9001", "bad")
    with pytest.raises(busanjin.BusanBusanjinContractError):
        busanjin.busan_busanjin_city_detail_url("334", "https://evil.example")


def test_complete_atomic_snapshot_duplicate_suppression_and_privacy() -> None:
    backend = _Backend()
    rows, parser, meta = _collect(backend)

    assert parser == busanjin.BUSAN_BUSANJIN_PARSER
    assert [row["provider_course_id"] for row in rows] == [
        f"{busanjin.BUSAN_BUSANJIN_PROVIDER}:district:9001",
        f"{busanjin.BUSAN_BUSANJIN_PROVIDER}:reserve:334:25009",
    ]
    assert meta["district_source_rows"] == 2
    assert meta["district_data_pages"] == 1
    assert meta["platform_source_rows"] == 2
    assert meta["platform_native_rows"] == 0
    assert meta["platform_external_duplicate_rows"] == 2
    assert meta["platform_semantic_censuses"] == 2
    assert meta["city_source_rows"] == 1
    assert meta["city_data_pages"] == 1
    assert meta["source_total"] == 5
    assert meta["unique_education_source_rows"] == 3
    assert meta["current_source_count"] == 2
    assert meta["expired_count"] == 1
    assert meta["returned_count"] == 2
    assert meta["required_list_requests"] == 11
    assert meta["list_requests"] == 11
    assert meta["sentinel_requests"] == 3
    assert meta["stability_rechecks"] == 4
    assert meta["detail_pages"] == 2
    assert meta["network_requests"] == 13
    assert meta["application_control_count"] == 1
    assert meta["offline_application_count"] == 1
    assert meta["status_counts"] == {"OPEN": 2}
    assert meta["duplicate_source_identity_count"] == 2
    assert meta["snapshot_complete"] is True
    assert meta["atomic_union_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert {row["branch"] for row in rows} == {
        "부산진구 평생학습관강좌",
        "부산진구 부암1동 주민자치회",
    }
    assert all(row["municipality_code"] == "2623000000" for row in rows)
    local, city = rows
    assert local["fee"] == "무료"
    assert local["raw_fields"]["source_fee"] == "무료"
    assert local["raw_fields"]["source_fee_label"] == "재료비"
    assert local["raw_fields"]["fee_evidence"] == (
        "official_lifelong_exact_owner_duplicate_list"
    )
    assert city["fee"] == "0 원"
    assert all(
        all(
            str(row.get(field) or "").strip()
            for field in (
                "target",
                "fee",
                "period",
                "venue_name",
                "category",
                "schedule_raw",
            )
        )
        for row in rows
    )
    assert local["reservation_available"] is True
    assert local["application_type"] == "ONLINE_RESERVATION"
    assert city["reservation_available"] is False
    assert city["application_type"] == "OFFLINE_APPLY"
    assert local["raw_fields"]["attachments_never_read"] is True
    assert local["raw_fields"]["free_form_detail_never_read"] is True
    assert city["raw_fields"]["attachments_never_read"] is True
    assert not any(busanjin.BUSAN_BUSANJIN_APPLY_PATH in url for url in backend.urls)

    serialized = repr(rows)
    for secret in (
        "SECRET_LOCAL_ATTACHMENT",
        "SECRET_LOCAL_FREE_FORM",
        "SECRET_APPLICANT_TABLE",
        "SECRET_CSRF_VALUE",
        "SECRET_PLATFORM_INSTRUCTOR",
        "SECRET_ENROLLMENT",
        "SECRET_CITY_CARD_PHONE",
        "SECRET_CITY_DETAIL_PHONE",
        "SECRET_CITY_ATTACHMENT",
        "SECRET_CITY_FREE_FORM",
        "private@example.test",
        "city@example.test",
        "010-1111-2222",
        "010-2222-3333",
        "051-800-9999",
        "051-800-8888",
    ):
        assert secret not in serialized


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
        "wrong_city_identity",
    ),
)
def test_any_ledger_contract_drift_discards_the_atomic_union(flag: str) -> None:
    backend = _Backend(**{flag: True})
    rows, _, meta = _collect(backend)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["atomic_union_complete"] is False
    assert meta["configured_collection_error"]


def test_transient_current_detail_is_retried_without_losing_exactness() -> None:
    backend = _Backend(transient_local_detail=True)
    rows, _, meta = _collect(backend)
    assert len(rows) == 2
    assert meta["network_retry_count"] == 1
    assert meta["network_requests"] == 14
    assert backend.calls["local-detail-9001"] == 2
    assert meta["snapshot_complete"] is True


def test_caps_dedupe_and_wrong_target_fail_closed() -> None:
    for kwargs in (
        {"max_pages": 10},
        {"detail_limit": 1},
        {"max_requests": 12},
    ):
        rows, _, meta = _collect(_Backend(), **kwargs)
        assert rows == []
        assert meta["source_cap_reached"] is True
        assert meta["configured_collection_error"]

    rows, _, meta = _collect(_Backend(), dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed atomic row count" in meta["configured_collection_error"]

    backend = _Backend()
    rows, _, meta = busanjin.collect_busan_busanjin_education(
        _target(provider="WRONG_PROVIDER"), fetcher=backend.fetch
    )
    assert rows == []
    assert backend.urls == []
    assert "target does not match" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_MUNICIPAL_AUDIT") != "1",
    reason="exact live municipal audit is opt-in",
)
def test_live_complete_three_ledger_snapshot_is_exact() -> None:
    rows, parser, meta = busanjin.collect_busan_busanjin_education(
        _target(),
        timeout=60,
        max_pages=250,
        detail_limit=300,
        max_requests=600,
        today="2026-07-22",
        max_workers=12,
    )
    assert parser == busanjin.BUSAN_BUSANJIN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["district_source_rows"] == 977
    assert meta["district_data_pages"] == 163
    assert meta["platform_source_rows"] == 977
    assert meta["platform_external_duplicate_rows"] == 977
    assert meta["platform_native_rows"] == 0
    assert meta["city_source_rows"] == 152
    assert meta["city_data_pages"] == 16
    assert meta["district_current_count"] == 100
    assert meta["city_current_count"] == 152
    assert meta["current_source_count"] == 252
    assert meta["returned_count"] == 252
    assert len(rows) == 252
    assert meta["required_list_requests"] == 188
    assert meta["detail_pages"] == 252
    assert meta["network_requests"] == 440
    assert all(row["municipality_code"] == "2623000000" for row in rows)
    assert len({row["provider_course_id"] for row in rows}) == 252
