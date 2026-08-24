from __future__ import annotations

from collections import Counter
import os
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_busan_junggu as junggu


class _Response:
    def __init__(self, url: str, html: str, status_code: int = 200):
        self.url = url
        self.content = html.encode("utf-8")
        self.status_code = status_code
        self.history: list[Any] = []
        self.headers = {"Content-Type": "text/html; charset=UTF-8"}


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target(
    provider: str = junggu.BUSAN_JUNGGU_PROVIDER,
    url: str = junggu.BUSAN_JUNGGU_CANONICAL_URL,
) -> dict[str, str]:
    return {"provider": provider, "url": url, "name": "부산 중구 교육"}


def _district_selects() -> str:
    result: list[str] = []
    for name, options in junggu._SELECT_OPTIONS.items():
        rendered = "".join(
            f'<option value="{value}">{label}</option>' for value, label in options
        )
        result.append(f'<select name="{name}">{rendered}</select>')
    return "".join(result)


def _district_row(
    identity: str,
    title: str,
    *,
    status: str,
    start: str,
    end: str,
    application: bool,
) -> str:
    state_class = {"대기중": "st1", "접수중": "st2", "마감": "st3"}[status]
    control = (
        f'<a href="{junggu.busan_junggu_application_url(identity)}">접수하러가기</a>'
        if application
        else "접수마감되었습니다."
    )
    return f"""
      <li><div class="box">
        <div class="state {state_class}"><span class="txt">{status}</span>
          <span class="targ">전체</span></div>
        <span class="data">2099-07-01 ~ 2099-07-31</span>
        <span class="tit"><a href="{junggu.busan_junggu_detail_url(identity)}">
          [평생학습프로그램]<br>{title}</a></span>
        <ul>
          <li><span class="name">교육기간</span> {start} ~ {end}</li>
          <li><span class="name">수강인원</span> 총 10명</li>
          <li><span class="name">신청/대기</span> 2 / 1명</li>
          <li><span class="name">접수방법</span> 온라인 , 전화</li>
        </ul>
        <span class="btn">{control}</span>
      </div></li>
    """


def _district_page(
    page: int,
    *,
    drift: bool = False,
    nonempty_sentinel: bool = False,
) -> str:
    rows = ""
    if page == 1:
        title = "미래 인문학" if not drift else "변경된 미래 인문학"
        rows = _district_row(
            "9001",
            title,
            status="접수중",
            start="2099-08-01",
            end="2099-08-31",
            application=True,
        )
        rows += _district_row(
            "9000",
            "지난 인문학",
            status="마감",
            start="2099-05-01",
            end="2099-05-31",
            application=False,
        )
    elif nonempty_sentinel:
        rows = _district_row(
            "9999",
            "경계 이탈 강좌",
            status="마감",
            start="2099-05-01",
            end="2099-05-31",
            application=False,
        )
    return f"""
      <html><head><title>{junggu._LIST_TITLE}</title></head><body>
        <form class="rfc_bbs_searchForm" method="get" action="/board/list.junggu">
          <input name="orderBy" value="">
          <input name="boardId" value="{junggu.BUSAN_JUNGGU_BOARD_ID}">
          <input name="menuCd" value="{junggu.BUSAN_JUNGGU_MENU_CODE}">
          <input name="contentsSid" value="1038">
          <input name="startPage" value="1">
          {_district_selects()}
        </form>
        <p class="boardPage">총게시물 : 2 건 / 페이지 : {page}/1</p>
        <div class="bbsEdu"><ul>{rows}</ul></div>
      </body></html>
    """


def _district_detail(*, wrong_title: bool = False) -> str:
    title = "다른 제목" if wrong_title else "미래 인문학"
    return f"""
      <html><head><title>{junggu._DETAIL_TITLE}</title></head><body>
        <form id="gradeFrm" name="gradeFrm" method="post"
          action="/menu/insertGradeAct.junggu"></form>
        <div class="bbs_vtype edu">
          <dl class="infor">
            <dt><span class="state">접수중</span> [ 2099년 ] {title}</dt>
            <dd class="edu"><ul>
              <li><span class="name">접수기간</span> 2099-07-01 09:00 ~ 2099-07-31 18:00</li>
              <li><span class="name">수강인원</span> 총 : 10명, 대기: 5명</li>
              <li><span class="name">접수인원</span> 2명</li>
              <li><span class="name">교육기간</span> 2099-08-01 ~ 2099-08-31</li>
              <li><span class="name">수강료</span> 무료</li>
              <li><span class="name">교육대상</span> 전체</li>
              <li><span class="name">교육시간</span> 화 10:00~12:00</li>
              <li><span class="name">교육횟수</span> 4회</li>
              <li><span class="name">접수방법</span> 온라인 , 전화</li>
              <li><span class="name">강사명</span> SECRET_INSTRUCTOR_010-1111-2222</li>
            </ul></dd>
          </dl>
          <div class="contents">
            <ul class="edu_infor">
              <li><span class="name">기관명</span> 중구 평생학습관</li>
              <li><span class="name">전화번호</span> SECRET_PHONE_051-600-0000</li>
              <li><span class="name">주소</span> SECRET_ADDRESS</li>
              <li><span class="name">소개</span> SECRET_INTRO</li>
            </ul>
            SECRET_FREE_FORM private@example.com
          </div>
        </div>
      </body></html>
    """


def _city_card(*, title: str = "주민센터 미술", branch: str = "중구 대청동 주민자치회") -> str:
    return f"""
      <ul class="reserveList"><li>
        <a class="reserveItem" href="javascript:void(0);"
          onclick="fn_viewProgrm('77', '8001');return false;">
          <div class="infoBox">
            <p class="tit" title="{title}">{title}</p>
            <span class="statusMark possible">접수중</span>
            <dl>
              <dt>기관</dt><dd>{branch}</dd>
              <dt>대상</dt><dd>제한없음</dd>
              <dt>장소</dt><dd>대청동 프로그램실</dd>
              <dt>일자</dt><dd class="date"><span>[신청] 2099-07-01 ~ 2099-07-31</span>
                <span>[행사] 2099-08-01 ~ 2099-08-31</span></dd>
              <dt>방법</dt><dd>온라인(선착순)</dd>
              <dt>문의</dt><dd>SECRET_CITY_PHONE_051-600-9999</dd>
            </dl>
          </div>
        </a>
      </li></ul>
    """


def _city_page(
    page: int,
    *,
    drift: bool = False,
    nonempty_sentinel: bool = False,
    wrong_owner: bool = False,
) -> str:
    card = ""
    if page == 1 or nonempty_sentinel:
        card = _city_card(
            title="변경된 주민센터 미술" if drift else "주민센터 미술",
            branch="서구 다른동 주민자치회" if wrong_owner else "중구 대청동 주민자치회",
        )
    return f"""
      <html><head><title>{junggu._CITY_LIST_TITLE}</title></head><body>
        <form id="srchForm" method="get" action="/lctre">
          <input name="curPage" value="{page}">
          <select name="srchGugun"><option value="15" selected>중구</option></select>
          <select name="srchResveInsttCd"><option value="33" selected>주민자치회</option></select>
        </form>
        {card}
        <div class="paginate"><a class="pgEnd"
          href="?curPage=1&amp;srchGugun=15&amp;srchResveInsttCd=33">마지막 목록으로</a></div>
      </body></html>
    """


def _city_detail(*, wrong_identity: bool = False, wrong_owner: bool = False) -> str:
    program = "8999" if wrong_identity else "8001"
    branch = "서구 다른동 주민자치회" if wrong_owner else "중구 대청동 주민자치회"
    values = (
        ("운영기간", "2099-08-01(토) ~ 2099-08-31(월)"),
        ("신청기간", "2099-07-01(수) 09:00 ~ 2099-07-31(금) 18:00"),
        ("취소여부", "취소 가능"),
        ("신청방법", "온라인(선착순)"),
        ("수강료", "0 원"),
        ("요일 /시간", "토 / 14:00 ~ 16:00"),
        ("문의전화", "SECRET_DETAIL_PHONE_051-600-8888"),
        ("운영기관", branch),
        ("대상", "제한없음"),
    )
    definitions = "".join(
        f"<dl><dt>{label}</dt><dd>{value}</dd></dl>" for label, value in values
    )
    return f"""
      <html><head><title>{junggu._CITY_LIST_TITLE}</title></head><body>
        <form id="viewForm" method="post">
          <input name="resveGroupSn" value="77">
          <input name="progrmSn" value="{program}">
          <div class="contHeader"><h3 class="titPage">주민센터 미술
            <span class="titState"><span class="statusMark possible">접수중</span></span>
          </h3></div>
          <div class="reserveStateWrap">
            <div class="reserveState"><div class="reserveStateInfo">{definitions}</div></div>
            <div class="reserveBtnWrap"><a class="btnTypeXL" href="#">신청하기</a></div>
          </div>
          <div class="reserveDetail">SECRET_CITY_FREE_FORM city-private@example.com</div>
        </form>
      </body></html>
    """


class _Backend:
    def __init__(
        self,
        *,
        bad_district_sentinel: bool = False,
        bad_city_sentinel: bool = False,
        district_drift: bool = False,
        city_drift: bool = False,
        wrong_city_owner: bool = False,
        wrong_city_identity: bool = False,
        wrong_district_detail_title: bool = False,
        transient_district_detail: bool = False,
    ) -> None:
        self.bad_district_sentinel = bad_district_sentinel
        self.bad_city_sentinel = bad_city_sentinel
        self.district_drift = district_drift
        self.city_drift = city_drift
        self.wrong_city_owner = wrong_city_owner
        self.wrong_city_identity = wrong_city_identity
        self.wrong_district_detail_title = wrong_district_detail_title
        self.transient_district_detail = transient_district_detail
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
        if parsed.hostname == junggu.BUSAN_JUNGGU_HOST:
            if parsed.path == junggu.BUSAN_JUNGGU_LIST_PATH:
                page = int((query.get("nowPage") or ["1"])[0])
                count = self._record(f"district-list-{page}", url)
                return _Response(
                    url,
                    _district_page(
                        page,
                        drift=self.district_drift and page == 1 and count >= 2,
                        nonempty_sentinel=self.bad_district_sentinel and page == 2,
                    ),
                )
            if parsed.path == junggu.BUSAN_JUNGGU_DETAIL_PATH:
                identity = (query.get("dataSid") or [""])[0]
                count = self._record(f"district-detail-{identity}", url)
                if self.transient_district_detail and count == 1:
                    return _Response(
                        url,
                        "<html><head><title>RFC 3.0 오류 메세지</title></head><body>temporary</body></html>",
                    )
                return _Response(
                    url,
                    _district_detail(wrong_title=self.wrong_district_detail_title),
                )
            if parsed.path == junggu.BUSAN_JUNGGU_APPLICATION_PATH:
                raise AssertionError("applicant/write form must never be fetched")
        if parsed.hostname == junggu.BUSAN_CITY_HOST:
            if parsed.path == junggu.BUSAN_CITY_LIST_PATH:
                page = int((query.get("curPage") or ["1"])[0])
                count = self._record(f"city-list-{page}", url)
                return _Response(
                    url,
                    _city_page(
                        page,
                        drift=self.city_drift and page == 1 and count >= 2,
                        nonempty_sentinel=self.bad_city_sentinel and page == 2,
                        wrong_owner=self.wrong_city_owner,
                    ),
                )
            if parsed.path == junggu.BUSAN_CITY_DETAIL_PATH:
                self._record("city-detail", url)
                return _Response(
                    url,
                    _city_detail(wrong_identity=self.wrong_city_identity),
                )
        raise AssertionError(f"unexpected fetch {url}")


def _collect(backend: _Backend, **kwargs: Any):
    return junggu.collect_busan_junggu_education(
        _target(),
        timeout=5,
        max_pages=kwargs.pop("max_pages", 2),
        detail_limit=kwargs.pop("detail_limit", 2),
        max_requests=kwargs.pop("max_requests", 20),
        today="2099-07-20",
        fetcher=backend.fetch,
        session_factory=backend.session,
        sleeper=lambda _seconds: None,
        max_workers=1,
        **kwargs,
    )


def test_candidate_classification_and_owner_boundary_are_explicit() -> None:
    assert set(junggu.BUSAN_JUNGGU_CANDIDATE_IDS.values()) == {
        "MUNI_IR_2BA97ED12CEB",
        "MUNI_IR_5E508121336B",
        "MUNI_IR_68AB15A0C263",
        "MUNI_IR_E858F721A742",
    }
    audit = junggu.BUSAN_JUNGGU_OWNER_BOUNDARY_AUDIT
    assert audit[junggu.BUSAN_JUNGGU_PROVIDER]["decision"] == (
        "canonical_district_education_owner"
    )
    assert audit[junggu.BUSAN_JUNGGU_FIXED_WRITE_PROVIDER]["decision"].endswith(
        "never_fetch"
    )
    assert audit["MUNI_IR_2BA97ED12CEB"]["operator"] == "부산박물관"
    assert audit["BUSAN_CITY_DETAIL_384_24458"]["operator"].startswith("서구 ")
    common = audit[junggu.BUSAN_LIFELONG_PROVIDER]
    assert common["office_code"] == "OFFICE_00002681"
    assert "suppress" in common["decision"]
    assert "50 of 51" in common["reason"]
    city = audit["OFFICIAL_BUSAN_CITY_RESERVATION"]
    assert city["filter"] == {"srchGugun": "15", "srchResveInsttCd": "33"}


def test_target_and_url_identity_contracts_are_exact() -> None:
    assert junggu.is_busan_junggu_education_target(_target())
    assert not junggu.is_busan_junggu_education_target(
        _target(url=junggu.BUSAN_JUNGGU_HOME_URL)
    )
    assert not junggu.is_busan_junggu_education_target(
        _target(provider=junggu.BUSAN_JUNGGU_FIXED_WRITE_PROVIDER)
    )
    assert junggu.busan_junggu_city_list_url(3).endswith(
        "curPage=3&srchGugun=15&srchResveInsttCd=33"
    )
    assert junggu.busan_junggu_city_detail_url("77", "8001").endswith(
        "resveGroupSn=77&progrmSn=8001"
    )
    with pytest.raises(junggu.BusanJungguContractError):
        junggu.busan_junggu_city_list_url(True)
    with pytest.raises(junggu.BusanJungguContractError):
        junggu.busan_junggu_city_detail_url("77", "https://evil.example")
    external_menu = (
        "https://www.bsjunggu.go.kr/yeyak/board/view.junggu?"
        "boardId=BBS_0000078&dataSid=9001&menuCd=ANOTHER_MENU"
    )
    assert junggu.canonical_busan_junggu_course_identity(external_menu) == (
        "BBS_0000078:9001"
    )


def test_complete_two_ledger_snapshot_and_pii_boundaries() -> None:
    backend = _Backend()
    rows, parser, meta = _collect(backend)

    assert parser == junggu.BUSAN_JUNGGU_PARSER
    assert len(rows) == 2
    assert meta["source_rows"] == 3
    assert meta["district_source_rows"] == 2
    assert meta["city_source_rows"] == 1
    assert meta["current_source_count"] == 2
    assert meta["district_current_count"] == 1
    assert meta["city_current_count"] == 1
    assert meta["expired_count"] == 1
    assert meta["list_requests"] == 8
    assert meta["required_list_requests"] == 8
    assert meta["sentinel_requests"] == 2
    assert meta["stability_rechecks"] == 4
    assert meta["detail_pages"] == 2
    assert meta["network_requests"] == 10
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert meta["application_control_count"] == 2
    assert {row["raw_fields"]["source_catalog"] for row in rows} == {
        "busan_junggu_district_board",
        "busan_reserve_junggu_resident_centres",
    }
    assert {row["branch"] for row in rows} == {
        "중구 평생학습관",
        "중구 대청동 주민자치회",
    }
    assert all(row["status"] == "OPEN" for row in rows)
    assert all(row["application_url"] == row["raw_url"] or "write.junggu" in row["application_url"] for row in rows)
    assert all(row["municipality_code"] == "2611000000" for row in rows)
    assert not any("/board/write.junggu" in url for url in backend.urls)

    serialized = repr(rows)
    for secret in (
        "SECRET_INSTRUCTOR",
        "SECRET_PHONE",
        "SECRET_ADDRESS",
        "SECRET_INTRO",
        "SECRET_FREE_FORM",
        "SECRET_CITY_PHONE",
        "SECRET_DETAIL_PHONE",
        "SECRET_CITY_FREE_FORM",
        "private@example.com",
        "city-private@example.com",
    ):
        assert secret not in serialized
    district = next(
        row
        for row in rows
        if row["raw_fields"]["source_catalog"] == "busan_junggu_district_board"
    )
    city = next(
        row
        for row in rows
        if row["raw_fields"]["source_catalog"]
        == "busan_reserve_junggu_resident_centres"
    )
    assert district["raw_fields"]["applicant_write_boundary_never_fetched"] is True
    assert district["raw_fields"]["instructor_value_never_read"] is True
    assert city["raw_fields"]["inquiry_phone_value_never_read"] is True
    assert city["raw_fields"]["free_form_detail_never_read"] is True


def test_transient_200_error_page_is_retried_without_weakening_atomicity() -> None:
    rows, _parser, meta = _collect(_Backend(transient_district_detail=True))
    assert len(rows) == 2
    assert meta["snapshot_complete"] is True
    assert meta["network_retry_count"] == 1
    assert meta["network_requests"] == 11


@pytest.mark.parametrize(
    ("flag", "needle"),
    (
        ("bad_district_sentinel", "post-final"),
        ("bad_city_sentinel", "sentinel"),
        ("district_drift", "stability recheck"),
        ("city_drift", "boundary page"),
        ("wrong_city_owner", "left Jung-gu owner"),
        ("wrong_city_identity", "detail identity changed"),
        ("wrong_district_detail_title", "list/detail title differs"),
    ),
)
def test_any_source_contract_failure_discards_both_ledgers(
    flag: str, needle: str
) -> None:
    rows, _parser, meta = _collect(_Backend(**{flag: True}))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert needle in meta["configured_collection_error"]


def test_caps_and_dedupe_change_fail_closed() -> None:
    rows, _parser, meta = _collect(_Backend(), max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(_Backend(), detail_limit=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(_Backend(), max_requests=7)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_requests cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(
        _Backend(), dedupe_rows=lambda values: values[:1]
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]


def test_only_identity_bound_legacy_archive_typos_are_normalized() -> None:
    assert junggu._list_education_date_range(
        "20240702 ~ 2024-08-06", identity="234995", label="legacy"
    ) == ("2024-07-02", "2024-08-06", True)
    assert junggu._list_education_date_range(
        "2022-09-05 ~ 2022-09-02", identity="219741", label="legacy"
    ) == ("2022-09-02", "2022-09-05", True)
    assert junggu._list_capacity("총 10명명", identity="189968") == (10, True)
    with pytest.raises(junggu.BusanJungguContractError):
        junggu._list_education_date_range(
            "20240702 ~ 2024-08-06", identity="999999", label="new row"
        )
    with pytest.raises(junggu.BusanJungguContractError):
        junggu._list_capacity("총 10명명", identity="999999")


def test_wrong_target_fails_before_network() -> None:
    backend = _Backend()
    rows, _parser, meta = junggu.collect_busan_junggu_education(
        _target(url=junggu.BUSAN_JUNGGU_HOME_URL),
        fetcher=backend.fetch,
        session_factory=backend.session,
    )
    assert rows == []
    assert backend.urls == []
    assert "exact canonical" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_MUNICIPAL_AUDIT") != "1",
    reason="set RUN_LIVE_MUNICIPAL_AUDIT=1 for the 240-request live audit",
)
def test_live_complete_snapshot_matches_latest_audit_floor() -> None:
    rows, _parser, meta = junggu.collect_busan_junggu_education(
        _target(), today="2026-07-22"
    )
    assert meta["snapshot_complete"] is True
    assert meta["district_source_rows"] >= 1540
    assert meta["city_source_rows"] >= 23
    assert meta["district_current_count"] >= 13
    assert meta["city_current_count"] >= 23
    assert len(rows) == meta["current_source_count"]
