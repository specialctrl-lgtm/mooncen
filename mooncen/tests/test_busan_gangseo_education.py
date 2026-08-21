from __future__ import annotations

from html import escape
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_busan_gangseo as gangseo


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
    provider: str = gangseo.BUSAN_GANGSEO_PROVIDER,
    url: str = gangseo.BUSAN_GANGSEO_CANONICAL_URL,
) -> dict[str, str]:
    return {"provider": provider, "url": url, "name": "부산 강서구 교육"}


def _rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for identity in range(10, 0, -1):
        current = identity >= 9
        rows.append(
            {
                "identity": str(identity),
                "title": f"강서 안전 교육 {identity}",
                "apply_start": "2099-07-01" if current else "2020-01-01",
                "apply_end": "2099-07-31" if current else "2020-01-31",
                "start": "2099-08-01" if current else "2020-02-01",
                "end": "2099-08-31" if current else "2020-02-28",
                "status": "접수중" if identity == 10 else "접수마감",
            }
        )
    return rows


def _local_row(row: dict[str, str], sequence: int) -> str:
    return f"""
      <tr><td></td><td class="pnum">{sequence}</td>
        <td><a href="/html/index.php?pCode=allprogram&amp;mode=lec.view&amp;idx={row['identity']}">
          <span class="ptit">{escape(row['title'])}</span>
          <span class="ptime"><span class="pmark">시간</span> 10:00~12:00</span>
          <span class="ptime"><span class="pmark">요일</span> 월</span>
        </a></td>
        <td><span class="ptime"><span class="pmark">부담</span> 무료</span>
          <span class="ptime"><span class="pmark">장소</span> 강서평생학습관</span></td>
        <td><span class="preg"><span class="pmark">신</span>
          {row['apply_start']} ~ {row['apply_end']}</span>
          <span class="pedu"><span class="pmark">교</span>
          {row['start']} ~ {row['end']}</span></td>
        <td>0/20</td><td><span>{row['status']}</span></td></tr>
    """


def _local_page(page: int, *, drift: bool = False, bad_sentinel: bool = False) -> str:
    rows = _rows()
    if drift:
        rows[0] = {**rows[0], "title": "변경된 강서 안전 교육"}
    body = ""
    if page == 1 or bad_sentinel:
        body = "".join(_local_row(row, 10 - index) for index, row in enumerate(rows))
    return f"""
      <html><head><title>전체 강좌보기&gt;강좌 | 부산강서평생학습</title></head>
      <body><table class="tbl-type01"><thead><tr>
        <th></th><th>번호</th><th>강의정보</th><th>본인부담/교육장소</th>
        <th>신청기간/교육기간</th><th>신청인원/마감정원</th><th>현황</th>
      </tr></thead><tbody>{body}</tbody></table>
      <a class="lastpage" href="?pCode=allprogram&amp;pg=1">[끝]</a>
      </body></html>
    """


def _detail(row: dict[str, str], *, wrong_title: bool = False) -> str:
    title = "다른 강좌" if wrong_title else row["title"]
    apply = (
        f'<p class="p-lec-abtn"><a class="bdp-btn" '
        f'href="/html/index.php?pCode=allprogram&amp;mode=lec.app&amp;lec_idx={row["identity"]}">'
        "<span>신청하기</span></a></p>"
        if row["status"] in {"접수중", "접수마감"}
        else ""
    )
    return f"""
      <html><head><title>전체 강좌보기&gt;강좌 | 부산강서평생학습</title></head>
      <body><table class="tbl-type02"><thead><tr><th colspan="4">
        <div class="p-tit-box"><span class="tit b">{escape(title)}</span>{apply}
          <p class="p-lec-abtn"><a href="/html/index.php?pCode=allprogram&amp;mode=lec.applist&amp;lec_idx={row['identity']}">
          신청목록(신청수정/취소하기)</a></p></div>
      </th></tr></thead><tbody>
        <tr><th>교육기관</th><td>평생학습관</td><th>교육분야</th><td>시민교육</td></tr>
        <tr><th>교육시간</th><td>오전</td><th>학습대상</th><td>성인</td></tr>
        <tr><th>교육분류</th><td colspan="3">평생학습관</td></tr>
        <tr><td colspan="4"></td></tr>
        <tr><th>강사정보</th><td colspan="3">SECRET INSTRUCTOR 010-1111-2222</td></tr>
        <tr><th>교육장소</th><td>강서평생학습관</td><th>교육대상</th><td>강서구민</td></tr>
        <tr><th>신청기간</th><td>{row['apply_start']} ~ {row['apply_end']}</td>
          <th>교육기간</th><td>{row['start']} ~ {row['end']}</td></tr>
        <tr><th>교육요일/횟수</th><td>월 (교육횟수 : 4회)</td><th></th><td></td></tr>
        <tr><th>수강인원</th><td>총 20명</td><th>수강자부담</th><td>무료</td></tr>
        <tr><th>교육기관</th><td>강서평생학습관</td><th>문의전화</th>
          <td>SECRET PHONE 051-999-9999</td></tr>
        <tr><th>신청상태</th><td colspan="3">{row['status']}</td></tr>
      </tbody></table><div>SECRET FREE FORM private@example.test</div></body></html>
    """


def _platform_row(row: dict[str, str], sequence: int) -> str:
    url = gangseo.busan_gangseo_detail_url(row["identity"])
    return f"""
      <tr><td>{sequence}</td><td class="subject">
        <a href="{escape(url, quote=True)}" target="_blank">
          <span class="tit">{escape(row['title'])}</span>
          <span class="org">{gangseo.BUSAN_LIFELONG_GANGSEO_OFFICE_NAME}</span>
        </a></td>
        <td><span>무료</span><span>SECRET INSTRUCTOR</span></td>
        <td><span class="s_type blue"><em class="hidden">교육기간</em>
          {row['start']}~{row['end']}<pre>월 10:00~12:00</pre></span></td>
        <td><span class="s_type indigo1"><em class="hidden">모집인원</em>20명</span>
          <span class="s_type red1"><em class="hidden">일반접수</em>
          {row['apply_start']}~{row['apply_end']}</span></td>
        <td><span class="s_type2"><em class="hidden">선착순</em></span>
          <span class="s_btn">{'접수중' if row['status'] == '접수중' else '마감'}</span></td>
        <td><a href="{escape(url, quote=True)}"><span>수강신청</span></a></td></tr>
    """


def _platform_page(
    page: int, *, drift: bool = False, unmatched: bool = False, bad_sentinel: bool = False
) -> str:
    rows = _rows()
    if drift:
        rows[0] = {**rows[0], "title": "변경된 플랫폼 교육"}
    if unmatched:
        rows[0] = {**rows[0], "identity": "9999"}
    body = ""
    if page == 1 or bad_sentinel:
        body = "".join(
            _platform_row(row, 10 - index) for index, row in enumerate(rows)
        )
    else:
        body = '<tr><td colspan="7">등록된 교육강좌가 없습니다.</td></tr>'
    return f"""
      <html><body><form id="learningVO" method="post"
        action="{gangseo._lifelong.BUSAN_LIFELONG_LIST_PATH}">
        <input name="inst_id" value="{gangseo.BUSAN_LIFELONG_GANGSEO_OFFICE}">
        <input name="display_type" value="2"><input name="pageIndex" value="{page}">
        <input name="pageUnit" value="100"><input name="l_search_ch" value="0">
        <select id="o_search_ch"><option value="{gangseo.BUSAN_LIFELONG_GANGSEO_OFFICE}" selected>
          {gangseo.BUSAN_LIFELONG_GANGSEO_OFFICE_NAME}</option></select>
        <select id="learning_state"><option value="0" selected>전체</option></select>
      </form><table><thead><tr>
        <th>번호</th><th>강좌명 / 교육기관</th><th>재료비 / 강사</th>
        <th>교육기간 / 교육시간</th><th>신청기간 / 접수인원</th><th>상태</th><th>보기</th>
      </tr></thead><tbody>{body}</tbody></table>
      <a class="page_nextend" href="?pageIndex=1"
        onclick="fn_list(1,'');return false;">마지막</a></body></html>
    """


def _city_page(page: int) -> str:
    return f"""
      <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head><body>
      <form id="srchForm" name="srchForm" method="get" action="/lctre">
        <input name="curPage" value="{page}">
        <select name="srchGugun"><option value="1" selected>강서구</option></select>
        <select name="srchResveInsttCd"><option value="33" selected>주민자치회</option></select>
      </form></body></html>
    """


def _fetcher(
    *,
    drift_recheck: bool = False,
    unmatched_platform: bool = False,
    bad_local_sentinel: bool = False,
    bad_platform_sentinel: bool = False,
):
    local_one_calls = 0
    platform_one_calls = 0

    def fetch(session: Any, url: str, timeout: int) -> _Response:
        nonlocal local_one_calls, platform_one_calls
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.hostname == gangseo.BUSAN_GANGSEO_HOST:
            if query.get("mode") == ["lec.view"]:
                identity = query["idx"][0]
                row = next(item for item in _rows() if item["identity"] == identity)
                return _Response(url, _detail(row))
            page = int(query.get("pg", ["1"])[0])
            if page == 1:
                local_one_calls += 1
            return _Response(
                url,
                _local_page(
                    page,
                    drift=drift_recheck and page == 1 and local_one_calls > 1,
                    bad_sentinel=bad_local_sentinel and page == 2,
                ),
            )
        if parsed.hostname == "lll.busan.go.kr":
            page = int(query.get("pageIndex", ["1"])[0])
            if page == 1:
                platform_one_calls += 1
            return _Response(
                url,
                _platform_page(
                    page,
                    unmatched=unmatched_platform,
                    bad_sentinel=bad_platform_sentinel and page == 2,
                ),
            )
        if parsed.hostname == gangseo.BUSAN_CITY_HOST:
            page = int(query.get("curPage", ["1"])[0])
            return _Response(url, _city_page(page))
        raise AssertionError(f"unexpected URL {url}")

    return fetch


def _collect(fetcher=None, **kwargs):
    return gangseo.collect_busan_gangseo_education(
        _target(),
        today="2099-01-01",
        timeout=3,
        max_pages=20,
        detail_limit=10,
        max_requests=40,
        max_workers=1,
        fetcher=fetcher or _fetcher(),
        session_factory=_Session,
        sleeper=lambda _: None,
        **kwargs,
    )


def test_target_requires_exact_provider_and_registered_or_canonical_url() -> None:
    assert gangseo.is_target(_target())
    assert gangseo.is_target(_target(url=gangseo.BUSAN_GANGSEO_REGISTERED_URL))
    assert not gangseo.is_target(_target(provider="OTHER"))
    assert not gangseo.is_target(_target(url="https://lll.bsgangseo.go.kr/"))


def test_url_and_identity_helpers_are_fail_closed() -> None:
    assert gangseo.busan_gangseo_list_url(2).endswith("pCode=allprogram&pg=2")
    detail = gangseo.busan_gangseo_detail_url("10")
    assert gangseo.canonical_busan_gangseo_identity(detail) == "10"
    assert not gangseo.canonical_busan_gangseo_identity(detail + "&extra=1")
    with pytest.raises(gangseo.BusanGangseoContractError):
        gangseo.busan_gangseo_detail_url("../10")


def test_official_local_scheduled_status_is_supported() -> None:
    row = {**_rows()[0], "status": "예정"}
    parsed = gangseo._parse_local_page(
        gangseo.BeautifulSoup(
            _local_page(1).replace("접수중", "예정", 1),
            "lxml",
        ),
        page=1,
    )[0][0]
    assert parsed["status"] == "SCHEDULED"
    assert parsed["reservation_available"] is False
    detail = gangseo._parse_local_detail(
        gangseo.BeautifulSoup(_detail(row), "lxml"),
        parsed["raw_url"],
        parsed,
    )
    assert detail["status"] == "SCHEDULED"
    assert detail["application_url"] == ""


def test_atomic_collector_suppresses_exact_platform_duplicates() -> None:
    rows, parser, meta = _collect()
    assert parser == gangseo.BUSAN_GANGSEO_PARSER
    assert [row["raw_fields"]["source_identity"] for row in rows] == ["10", "9"]
    assert meta["district_source_rows"] == 10
    assert meta["platform_source_rows"] == 10
    assert meta["platform_external_duplicate_rows"] == 10
    assert meta["unique_education_source_rows"] == 10
    assert meta["returned_count"] == 2
    assert meta["network_requests"] == 13
    assert meta["snapshot_complete"] is True
    assert rows[0]["reservation_available"] is True
    assert rows[1]["reservation_available"] is False
    assert rows[1]["raw_fields"]["closed_application_control_retained"] is True
    assert meta["closed_application_control_retained_count"] == 1
    serialized = repr(rows)
    assert "SECRET" not in serialized
    assert "010-1111-2222" not in serialized
    assert "private@example.test" not in serialized


@pytest.mark.parametrize(
    "fetcher",
    [
        _fetcher(unmatched_platform=True),
        _fetcher(bad_local_sentinel=True),
        _fetcher(bad_platform_sentinel=True),
        _fetcher(drift_recheck=True),
    ],
)
def test_atomic_collector_fails_closed_on_source_drift(fetcher) -> None:
    rows, _, meta = _collect(fetcher=fetcher)
    assert rows == []
    assert meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_caps_fail_closed_before_partial_results() -> None:
    rows, _, meta = gangseo.collect_busan_gangseo_education(
        _target(), max_pages=2, fetcher=_fetcher(), session_factory=_Session
    )
    assert rows == []
    assert meta["source_cap_reached"] is True


def test_platform_application_deadline_may_only_lag_owner() -> None:
    owner = {"title": "교육", "start_date": "2099-01-01", "end_date": "2099-02-01", "apply_start": "2098-12-01", "apply_end": "2098-12-31"}
    lag = {**owner, "apply_end": "2098-12-30"}
    future = {**owner, "apply_end": "2099-01-01"}
    assert gangseo._same_owner_fields(lag, owner)
    assert not gangseo._same_owner_fields(future, owner)


def test_wrong_detail_title_is_rejected() -> None:
    row = gangseo._parse_local_page(
        gangseo.BeautifulSoup(_local_page(1), "lxml"), page=1
    )[0][0]
    with pytest.raises(gangseo.BusanGangseoContractError):
        gangseo._parse_local_detail(
            gangseo.BeautifulSoup(_detail(_rows()[0], wrong_title=True), "lxml"),
            row["raw_url"],
            row,
        )


@pytest.mark.skipif(
    os.getenv("RUN_BUSAN_GANGSEO_LIVE_TEST") != "1",
    reason="set RUN_BUSAN_GANGSEO_LIVE_TEST=1 for exact live census",
)
def test_live_exact_gangseo_snapshot() -> None:
    rows, parser, meta = gangseo.collect_busan_gangseo_education(
        _target(),
        today="2026-07-22",
        timeout=35,
        max_pages=150,
        detail_limit=100,
        max_requests=350,
        max_workers=12,
    )
    assert parser == gangseo.BUSAN_GANGSEO_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["district_source_rows"] == 1016
    assert meta["district_data_pages"] == 102
    assert meta["district_current_count"] == 18
    assert meta["platform_source_rows"] == 100
    assert meta["platform_external_duplicate_rows"] == 100
    assert meta["platform_native_rows"] == 0
    assert meta["city_source_rows"] == 0
    assert meta["network_requests"] == 130
    assert len(rows) == 18
    assert meta["snapshot_complete"] is True
