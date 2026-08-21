from __future__ import annotations

from html import escape
import os
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_busan_haeundae as haeundae


class _Response:
    def __init__(self, url: str, html: str, status_code: int = 200) -> None:
        self.url = url
        self.content = html.encode("utf-8")
        self.text = html
        self.status_code = status_code
        self.history: list[Any] = []


class _Session:
    def close(self) -> None:
        return None


def _target(
    *,
    provider: str = haeundae.BUSAN_HAEUNDAE_PROVIDER,
    candidate_id: str = haeundae.BUSAN_HAEUNDAE_CANDIDATE_ID,
    url: str = haeundae.BUSAN_HAEUNDAE_CANONICAL_URL,
) -> dict[str, str]:
    return {
        "provider": provider,
        "candidate_id": candidate_id,
        "url": url,
        "name": "부산광역시 해운대구 교육",
    }


_LOCAL_ROWS = (
    {
        "identity": "2099070001",
        "title": "해운대 미래교실",
        "status": "접수",
        "status_class": "ico1",
        "detail_status": "접수중",
        "start": "2099-08-01",
        "end": "2099-08-31",
        "weekday": "수",
    },
    {
        "identity": "2099070002",
        "title": "해운대 시민강좌",
        "status": "대기중",
        "status_class": "ico2",
        "detail_status": "접수대기",
        "start": "2099-09-01",
        "end": "2099-09-30",
        "weekday": "",
        "fee": "",
    },
)


def _local_card(row: dict[str, str]) -> str:
    identity = row["identity"]
    detail = (
        f"/reserve/index.do?menuCd={haeundae.BUSAN_HAEUNDAE_DETAIL_MENU}"
        f"&amp;res_no={identity}"
    )
    return f"""
      <div id="ae{identity}" class="reserVbox clearfix">
        <div class="base">
          <a href="{detail}"><strong class="title">{escape(row['title'])}</strong></a>
          <ul>
            <li>교육기간 : {row['start']} ~ {row['end']}</li>
            <li>신청기간 : 2099-07-01 ~ 2099-07-31</li>
            <li>교육시간 : 10:00 ~ 12:00</li>
            <li>모집인원 : 20명</li>
            <li>교육장소 : 해운대구 평생학습관</li>
            <li>접수현황 : SECRET_LOCAL_ENROLLMENT 3 / 20</li>
          </ul>
        </div>
        <div class="btn_reserv">
          <span class="head {row['status_class']}">{row['status']}</span>
        </div>
      </div>
    """


def _local_page(page: int, *, bad_sentinel: bool = False) -> str:
    cards = ""
    if page == 1 or bad_sentinel:
        cards = "".join(_local_card(row) for row in _LOCAL_ROWS)
    section = f'<section class="reserV">{cards}</section>' if cards else ""
    return f"""
      <html><head><title>전체 | 해운대구청</title></head><body>
        <form id="searchForm" name="searchForm" method="get">
          <input name="menuCd" value="{haeundae.BUSAN_HAEUNDAE_LIST_MENU}">
          <input name="paga_no" value="1">
          <p class="articles">전체 2, 현재 페이지 {page} / 1</p>
        </form>
        {section}
      </body></html>
    """


def _local_detail(row: dict[str, str]) -> str:
    identity = row["identity"]
    weekday = (
        f"<li><span>교육요일</span>{escape(row['weekday'])}</li>"
        if row["weekday"]
        else ""
    )
    if row["detail_status"] == "접수중":
        control = (
            f'<a href="/reserve/index.do?menuCd='
            f'{haeundae.BUSAN_HAEUNDAE_APPLY_MENU}&amp;res_no={identity}">신청하기</a>'
        )
    else:
        control = '<a href="#">접수대기</a>'
    return f"""
      <html><head><title>강좌 상세 | 해운대구청</title></head><body>
        <h1 id="tit_cont">{escape(row['title'])}</h1>
        <form name="frm"><input name="res_no" value="{identity}"></form>
        <div class="reserWrap"><ul>
          <li><h4>신청기간</h4><div class="cont">2099-07-01 ~ 2099-07-31</div></li>
          <li><h4>신청인원</h4><div class="cont">SECRET_DETAIL_ENROLLMENT</div></li>
          <li><h4>교육정보</h4><ul>
            <li><span>교육기간</span>{row['start']} ~ {row['end']}</li>
            <li><span>교육시간</span>10:00 ~ 12:00</li>
            {weekday}
            <li><span>수강금액</span>{escape(row.get('fee', '무료'))}</li>
            <li><span>교육장소</span>해운대구 평생학습관</li>
          </ul></li>
          <li><h4>{row['detail_status']}</h4><div class="cont">{control}</div></li>
        </ul></div>
        <div class="reserCont">SECRET_FREE_FORM 010-2222-3333 private@example.test</div>
      </body></html>
    """


def _platform_page(page: int, *, drift: bool = False) -> str:
    external = (
        f"https://{haeundae.BUSAN_HAEUNDAE_HOST}{haeundae.BUSAN_HAEUNDAE_PATH}?"
        f"menuCd={haeundae.BUSAN_HAEUNDAE_PLATFORM_DETAIL_MENU}&amp;"
        f"res_no={_LOCAL_ROWS[0]['identity']}"
    )
    if page == 1:
        title = "변경된 강좌" if drift else _LOCAL_ROWS[0]["title"]
        body = f"""
          <tr>
            <td>1</td>
            <td class="subject"><a href="{external}" target="_blank">
              <span class="tit">{escape(title)}</span>
              <span class="org">해운대구청</span></a></td>
            <td><span>무료</span><span>SECRET_LIST_INSTRUCTOR</span></td>
            <td><span class="s_type blue"><em class="hidden">교육기간</em>
              2099-08-01~2099-08-31<pre>수 10:00~12:00</pre></span></td>
            <td><span class="s_type indigo1"><em class="hidden">모집인원</em>20명</span>
              <span class="s_type red1"><em class="hidden">일반접수</em>
              2099-07-01~2099-07-31 (접수인원 : SECRET)</span></td>
            <td><span class="s_type2 mint"><em class="hidden">선발방법</em>선착순</span>
              <span class="s_btn blue">접수중</span></td>
            <td><a href="{external}">수강신청</a></td>
          </tr>
        """
    else:
        body = '<tr><td colspan="7">등록된 교육강좌가 없습니다.</td></tr>'
    return f"""
      <html><head><title>부산평생학습플랫폼</title></head><body>
        <form id="learningVO" method="post"
          action="{haeundae.BUSAN_LIFELONG_LIST_PATH}">
          <input name="inst_id" value="{haeundae.BUSAN_LIFELONG_HAEUNDAE_OFFICE}">
          <input name="display_type" value="2">
          <input name="pageIndex" value="{page}">
          <input name="l_search_ch" value="0">
          <select id="o_search_ch"><option
            value="{haeundae.BUSAN_LIFELONG_HAEUNDAE_OFFICE}" selected>해운대구청</option></select>
          <select id="learning_state"><option value="0" selected>전체</option></select>
        </form>
        <table><thead><tr>
          <th>번호</th><th>강좌명 / 교육기관</th><th>재료비 / 강사</th>
          <th>교육기간 / 교육시간</th><th>신청기간 / 접수인원</th>
          <th>상태</th><th>보기</th>
        </tr></thead><tbody>{body}</tbody></table>
        <a class="page_nextend" href="?pageIndex=1"
          onclick="fn_list(1,'');return false;">마지막</a>
      </body></html>
    """


def _city_page(page: int, *, wrong_owner: bool = False) -> str:
    branch = "부산진구 전포1동 주민자치회" if wrong_owner else "해운대구 중1동 주민자치회"
    listing = ""
    if page == 1:
        listing = f"""
          <ul class="reserveList"><li>
            <a class="reserveItem" href="#"
              onclick="fn_viewProgrm('33', '9001'); return false;">
              <span class="statusMark">접수중</span>
              <strong class="tit" title="주민 디지털 교실">주민 디지털 교실</strong>
              <div class="infoBox"><dl>
                <dt>기관</dt><dd>{branch}</dd>
                <dt>대상</dt><dd>해운대구민</dd>
                <dt>장소</dt><dd>중1동 행정복지센터</dd>
                <dt>일자</dt><dd>[신청] 2099-07-01 ~ 2099-07-31 [행사] 2099-08-01 ~ 2099-08-31</dd>
                <dt>방법</dt><dd>온라인(접수 후 개별 통보)</dd>
                <dt>문의</dt><dd>SECRET_CITY_CARD_PHONE 051-000-0000</dd>
              </dl></div>
            </a>
          </li></ul>
        """
    return f"""
      <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head><body>
        <form id="srchForm" name="srchForm" method="get" action="/lctre">
          <input name="curPage" value="{page}">
          <select name="srchGugun"><option value="16" selected>해운대구</option></select>
          <select name="srchResveInsttCd"><option value="33" selected>주민자치</option></select>
        </form>
        {listing}
        <div class="paginate"><a class="pgEnd"
          href="/lctre/list?curPage=1&amp;srchGugun=16&amp;srchResveInsttCd=33">끝</a></div>
      </body></html>
    """


def _city_detail() -> str:
    fields = (
        ("운영기간", "2099-08-01 ~ 2099-08-31"),
        ("신청기간", "2099-07-01 ~ 2099-07-31"),
        ("취소여부", "가능"),
        ("신청방법", "온라인(접수 후 개별 통보)"),
        ("수강료", "무료"),
        ("요일 /시간", "수 14:00 ~ 16:00"),
        ("문의전화", "SECRET_CITY_DETAIL_PHONE 051-000-0000"),
        ("운영기관", "해운대구 중1동 주민자치회"),
        ("대상", "해운대구민"),
    )
    definitions = "".join(
        f"<dl><dt>{escape(label)}</dt><dd>{escape(value)}</dd></dl>"
        for label, value in fields
    )
    return f"""
      <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head><body>
        <form id="viewForm" method="post">
          <input name="resveGroupSn" value="33">
          <input name="progrmSn" value="9001">
          <div class="contHeader"><h3 class="titPage">주민 디지털 교실
            <span class="statusMark">접수중</span></h3></div>
          <div class="reserveStateWrap">
            <div class="reserveStateInfo">{definitions}</div>
            <div class="reserveBtnWrap"><a class="btnTypeXL">예약하기</a></div>
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
        platform_drift: bool = False,
        wrong_city_owner: bool = False,
    ) -> None:
        self.bad_local_sentinel = bad_local_sentinel
        self.platform_drift = platform_drift
        self.wrong_city_owner = wrong_city_owner
        self.urls: list[str] = []
        self._platform_page_one_calls = 0
        self._lock = Lock()

    def session(self) -> _Session:
        return _Session()

    def fetch(self, _session: Any, url: str, _timeout: int) -> _Response:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        with self._lock:
            self.urls.append(url)
        if parsed.hostname == haeundae.BUSAN_HAEUNDAE_HOST:
            menu = query.get("menuCd", [""])[0]
            if menu == haeundae.BUSAN_HAEUNDAE_LIST_MENU:
                page = int(query["page_no"][0])
                return _Response(
                    url,
                    _local_page(page, bad_sentinel=self.bad_local_sentinel),
                )
            if menu == haeundae.BUSAN_HAEUNDAE_DETAIL_MENU:
                identity = query["res_no"][0]
                row = next(value for value in _LOCAL_ROWS if value["identity"] == identity)
                return _Response(url, _local_detail(row))
        if (
            parsed.hostname == "lll.busan.go.kr"
            and parsed.path == haeundae.BUSAN_LIFELONG_LIST_PATH
        ):
            page = int(query["pageIndex"][0])
            drift = False
            if page == 1:
                with self._lock:
                    self._platform_page_one_calls += 1
                    drift = self.platform_drift and self._platform_page_one_calls > 1
            return _Response(url, _platform_page(page, drift=drift))
        if (
            parsed.hostname == haeundae.BUSAN_CITY_HOST
            and parsed.path == haeundae.BUSAN_CITY_LIST_PATH
        ):
            page = int(query["curPage"][0])
            return _Response(url, _city_page(page, wrong_owner=self.wrong_city_owner))
        if (
            parsed.hostname == haeundae.BUSAN_CITY_HOST
            and parsed.path == haeundae.BUSAN_CITY_DETAIL_PATH
        ):
            return _Response(url, _city_detail())
        raise AssertionError(f"unexpected URL {url}")


def _collect(
    backend: _Backend,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    return haeundae.collect_busan_haeundae_education(
        _target(),
        today="2099-01-01",
        max_pages=kwargs.pop("max_pages", 2),
        detail_limit=kwargs.pop("detail_limit", 4),
        max_requests=kwargs.pop("max_requests", 20),
        max_workers=2,
        fetcher=backend.fetch,
        session_factory=backend.session,
        sleeper=lambda _seconds: None,
        **kwargs,
    )


def test_exact_target_retargets_registered_static_page_and_locks_ownership() -> None:
    assert haeundae.BUSAN_HAEUNDAE_REGISTERED_URL != haeundae.BUSAN_HAEUNDAE_URL
    assert haeundae.is_busan_haeundae_education_target(_target()) is True
    assert (
        haeundae.BUSAN_HAEUNDAE_OWNER_BOUNDARY_AUDIT[
            haeundae.BUSAN_HAEUNDAE_PROVIDER
        ]["decision"]
        == "retain_provider_and_retarget_static_page_to_complete_owner"
    )
    assert (
        haeundae.BUSAN_HAEUNDAE_OWNER_BOUNDARY_AUDIT[
            haeundae.BUSAN_LIFELONG_PROVIDER
        ]["office_code"]
        == "OFFICE_00002635"
    )
    assert not haeundae.is_busan_haeundae_education_target(
        _target(url=haeundae.BUSAN_HAEUNDAE_REGISTERED_URL)
    )
    assert not haeundae.is_busan_haeundae_education_target(
        _target(candidate_id="MUNI_IR_WRONG")
    )


def test_complete_atomic_three_ledger_snapshot_and_privacy() -> None:
    backend = _Backend()
    rows, parser, meta = _collect(backend)

    assert parser == haeundae.BUSAN_HAEUNDAE_PARSER
    assert [row["provider_course_id"] for row in rows] == [
        f"{haeundae.BUSAN_HAEUNDAE_PROVIDER}:district:2099070001",
        f"{haeundae.BUSAN_HAEUNDAE_PROVIDER}:district:2099070002",
        f"{haeundae.BUSAN_HAEUNDAE_PROVIDER}:reserve:33:9001",
    ]
    assert meta["district_source_rows"] == 2
    assert meta["district_publishable_rows"] == 2
    assert meta["district_current_count"] == 2
    assert meta["platform_source_rows"] == 1
    assert meta["platform_external_duplicate_rows"] == 1
    assert meta["platform_external_unique_resnos"] == 1
    assert meta["platform_external_matching_current_district"] == 1
    assert meta["platform_native_rows"] == 0
    assert meta["city_source_rows"] == 1
    assert meta["city_current_count"] == 1
    assert meta["source_total"] == 4
    assert meta["duplicate_source_rows"] == 1
    assert meta["unique_education_source_rows"] == 3
    assert meta["current_source_count"] == 3
    assert meta["status_counts"] == {"OPEN": 2, "SCHEDULED": 1}
    assert meta["application_control_count"] == 2
    assert meta["list_requests"] == 10
    assert meta["detail_pages"] == 3
    assert meta["network_requests"] == 13
    assert meta["sentinel_requests"] == 4
    assert meta["stability_rechecks"] == 4
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert rows[0]["raw_fields"]["detail_education_weekday"] == "수"
    assert rows[1]["raw_fields"]["detail_education_weekday"] == ""
    assert all(
        row["target"] == "대상 별도 안내"
        for row in rows
        if ":district:" in row["provider_course_id"]
    )
    assert (
        rows[1]["raw_fields"]["target_evidence"]
        == "official_district_detail_omits_target_field"
    )
    assert rows[1]["fee"] == "요금 별도 안내"
    assert (
        rows[1]["raw_fields"]["fee_evidence"]
        == "official_district_detail_empty_fee"
    )
    assert all(row["municipality_code"] == "2635000000" for row in rows)

    serialized = repr(rows)
    for secret in (
        "SECRET_LOCAL_ENROLLMENT",
        "SECRET_DETAIL_ENROLLMENT",
        "SECRET_FREE_FORM",
        "SECRET_LIST_INSTRUCTOR",
        "SECRET_CITY_CARD_PHONE",
        "SECRET_CITY_DETAIL_PHONE",
        "SECRET_CITY_FREE_FORM",
        "private@example.test",
        "city-private@example.test",
        "010-2222-3333",
        "051-000-0000",
    ):
        assert secret not in serialized
    assert not any("apply" in urlparse(url).path.casefold() for url in backend.urls)


@pytest.mark.parametrize(
    ("flag", "needle"),
    (
        ("bad_local_sentinel", "row count changed"),
        ("platform_drift", "complete censuses changed"),
        ("wrong_city_owner", "left Haeundae owner"),
    ),
)
def test_any_ledger_contract_failure_discards_the_whole_snapshot(
    flag: str, needle: str
) -> None:
    rows, _parser, meta = _collect(_Backend(**{flag: True}))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert needle.casefold() in meta["configured_collection_error"].casefold()


def test_audited_platform_application_extension_is_identity_bound() -> None:
    identity = "2026070006"
    district = {
        "title": "뉴스 탐정단",
        "start_date": "2026-08-06",
        "end_date": "2026-08-27",
        "apply_start": "2026-07-13",
        "apply_end": "2026-07-27",
    }
    external = {
        "title": "뉴스 탐정단",
        "start_date": "2026-08-06",
        "end_date": "2026-08-27",
        "apply_start": "2026-07-13",
        "apply_end": "2026-07-20",
        "raw_url": (
            f"https://{haeundae.BUSAN_HAEUNDAE_HOST}"
            f"{haeundae.BUSAN_HAEUNDAE_PATH}?"
            f"menuCd={haeundae.BUSAN_HAEUNDAE_PLATFORM_DETAIL_MENU}&"
            f"res_no={identity}"
        ),
        "raw_fields": {"identity_kind": "external"},
    }
    assert (
        haeundae._prove_platform_duplicate(external, {identity: district})
        == identity
    )

    with pytest.raises(
        haeundae.BusanHaeundaeContractError,
        match="application-date drift changed",
    ):
        haeundae._prove_platform_duplicate(
            external,
            {identity: {**district, "apply_end": "2026-07-28"}},
        )


def test_caps_and_wrong_target_fail_before_partial_publication() -> None:
    rows, _parser, meta = _collect(_Backend(), detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]

    backend = _Backend()
    rows, _parser, meta = haeundae.collect_busan_haeundae_education(
        _target(url=haeundae.BUSAN_HAEUNDAE_REGISTERED_URL),
        fetcher=backend.fetch,
        session_factory=backend.session,
    )
    assert rows == []
    assert backend.urls == []
    assert "exact canonical" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("MOONCEN_RUN_BUSAN_HAEUNDAE_LIVE") != "1",
    reason=(
        "set MOONCEN_RUN_BUSAN_HAEUNDAE_LIVE=1 for the exact 249-request "
        "Haeundae audit"
    ),
)
def test_live_exact_snapshot_matches_2026_07_22_audit() -> None:
    rows, parser, meta = haeundae.collect_busan_haeundae_education(
        _target(), today="2026-07-22"
    )
    assert parser == haeundae.BUSAN_HAEUNDAE_PARSER
    assert len(rows) == 184
    assert meta["snapshot_complete"] is True
    assert meta["district_source_rows"] == 728
    assert meta["district_data_pages"] == 46
    assert meta["district_excluded_non_course_rows"] == 7
    assert meta["district_current_count"] == 101
    assert meta["platform_source_rows"] == 132
    assert meta["platform_external_duplicate_rows"] == 132
    assert meta["platform_external_matching_current_district"] == 130
    assert meta["platform_external_audited_tombstones"] == 2
    assert meta["platform_native_rows"] == 0
    assert meta["city_source_rows"] == 83
    assert meta["city_data_pages"] == 9
    assert meta["city_current_count"] == 83
    assert meta["source_total"] == 943
    assert meta["unique_education_source_rows"] == 804
    assert meta["status_counts"] == {
        "OPEN": 53,
        "SCHEDULED": 7,
        "CLOSED": 124,
    }
    assert meta["application_control_count"] == 23
    assert meta["required_list_requests"] == 65
    assert meta["required_detail_requests"] == 184
    assert meta["network_requests"] == 249
    assert meta["sentinel_requests"] == 4
    assert meta["stability_rechecks"] == 6
    assert meta["network_retry_count"] == 0
