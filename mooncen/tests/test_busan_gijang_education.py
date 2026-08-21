from __future__ import annotations

from html import escape
import os
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_busan_gijang as gijang


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
    provider: str = gijang.BUSAN_GIJANG_PROVIDER,
    url: str = gijang.BUSAN_GIJANG_CANONICAL_URL,
) -> dict[str, str]:
    return {"provider": provider, "url": url, "name": "기장군 평생학습"}


def _rows() -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for identity in range(10, 0, -1):
        current = identity >= 9
        result.append({
            "identity": str(identity),
            "title": f"기장 안전교육 {identity}",
            "category": "일반평생교육강좌",
            "apply_start": "2099-07-01" if current else "2020-01-01",
            "apply_end": "2099-07-31" if current else "2020-01-31",
            "start": "2099-08-01" if current else "2020-02-01",
            "end": "2099-08-31" if current else "2020-02-28",
            "status": "접수중" if identity == 10 else "접수마감" if identity == 9 else "교육완료",
        })
    return result


def _local_card(row: Mapping[str, str]) -> str:
    return f"""
      <dl class="no_app"><dt><span>{escape(row['category'])}</span></dt><dd>
        <div><span>{row['status']}</span></div>
        <a href="?menuCd={gijang.BUSAN_GIJANG_MENU}&amp;idx={row['identity']}&amp;mode=view">
          <p class="tit">{escape(row['title'])}</p><ul>
            <li><b>신청기간</b><span>{row['apply_start']} ~ {row['apply_end']}</span></li>
            <li><b>교육기간</b><span>{row['start']} ~ {row['end']}</span></li>
            <li><b>교육장소</b><span>기장평생학습관</span></li>
            <li><b>모집인원(신청/대기)</b><span>20/3명</span></li>
            <li><b>접수인원(신청/대기)</b><span>5/0명</span></li>
          </ul></a></dd></dl>
    """


def _local_page(page: int, *, drift: bool = False, bad_sentinel: bool = False) -> str:
    rows = _rows()
    if drift:
        rows[0] = {**rows[0], "title": "변경된 기장 안전교육"}
    body = "".join(_local_card(row) for row in rows) if page == 1 or bad_sentinel else ""
    return f"""
      <html><head><title>평생학습정보 &gt; 온라인 수강신청</title></head><body>
      <form id="listForm" name="listForm" method="GET">
        <input name="menuCd" value="{gijang.BUSAN_GIJANG_MENU}">
        <input name="mode" value="list"><input name="pageIndex" value="{page}">
        <input name="searchCategory" value=""><input name="searchCondition" value="LECT_NM">
        <input name="searchKeyword" value="">
      </form><div class="pro_applylist">{body}</div>
      <div class="pagination"><a class="last01" href="#" onclick="linkPage(1); return false;">마지막으로</a></div>
      </body></html>
    """


def _detail_row(label: str, value: str) -> str:
    return f"<tr><th>{label}</th><td>{value}</td></tr>"


def _local_detail(row: Mapping[str, str], *, wrong_title: bool = False) -> str:
    title = "다른 강좌" if wrong_title else row["title"]
    values = {
        "강사명": "SECRET INSTRUCTOR 010-1111-2222",
        "수강대상": "기장군민", "교육과정": row["category"], "강의실": "기장평생학습관",
        "교육기간": f"{row['start']} ~ {row['end']}", "교육시간": "10:00 ~ 12:00",
        "총 교육시간": "SECRET HOURS", "요일": "월", "접수방법": "인터넷",
        "신청상태": row["status"], "재료비": "무료", "수강료": "무료",
        "연락처": "SECRET PHONE 051-999-9999",
        "접수기간": f"{row['apply_start']} (09:00) ~ {row['apply_end']} (18:00)",
        "모집인원": "SECRET ENROLMENT", "접수인원": "SECRET APPLICANTS",
        "강좌소개": "SECRET FREE FORM private@example.test", "참고사항": "SECRET NOTE",
        "첨부파일": "SECRET ATTACHMENT",
    }
    rows = "".join(_detail_row(label, values[label]) for label in gijang._LOCAL_DETAIL_LABELS)
    control = (
        f'<a class="btn done application" data-idx="{row["identity"]}" '
        f'data-menucd="{gijang.BUSAN_GIJANG_MENU}" href="#" onclick="return false;">신청하기</a>'
        if row["status"] == "접수중" else ""
    )
    return f"""
      <html><body><div class="conts" id="conts"><h3>{escape(title)}</h3>
      <table class="tbl_lll Tbody"><tbody>{rows}</tbody></table>
      <div class="taC">{control}<a class="btn default">목록</a></div>
      <div>SECRET OUTSIDE DETAIL private@example.test</div></div></body></html>
    """


def _platform_row(row: Mapping[str, str], sequence: int) -> str:
    url = gijang.busan_gijang_detail_url(row["identity"])
    status = "접수중" if row["status"] == "접수중" else "마감"
    return f"""
      <tr><td>{sequence}</td><td class="subject">
        <a href="{escape(url, quote=True)}" target="_blank">
          <span class="tit">{escape(row['title'])}</span>
          <span class="org">{gijang.BUSAN_LIFELONG_GIJANG_OFFICE_NAME}</span></a></td>
        <td><span>무료</span><span>SECRET INSTRUCTOR</span></td>
        <td><span class="s_type blue"><em class="hidden">교육기간</em>
          {row['start']}~{row['end']}<pre>월, 10:00~12:00</pre></span></td>
        <td><span class="s_type indigo1"><em class="hidden">모집인원</em>20명</span>
          <span class="s_type red1"><em class="hidden">일반접수</em>
          {row['apply_start']}~{row['apply_end']}</span></td>
        <td><span class="s_type2"><em class="hidden">선착순</em></span>
          <span class="s_btn">{status}</span></td>
        <td><a href="{escape(url, quote=True)}"><span>수강신청</span></a></td></tr>
    """


def _platform_native(sequence: int = 1) -> str:
    identity = "LEARNING_00087619"
    onclick = f"fn_learning_detail('{identity}'); return false;"
    return f"""
      <tr><td>{sequence}</td><td class="subject">
        <a href="javascript:;" onclick="{onclick}"><span class="tit">테스트1</span>
          <span class="org">{gijang.BUSAN_LIFELONG_GIJANG_OFFICE_NAME}</span></a></td>
        <td><span>무료</span><span>SECRET</span></td>
        <td><span class="s_type blue"><em class="hidden">교육기간</em>
          2099-01-01~2099-01-01<pre>수, 12:00~13:00</pre></span></td>
        <td><span class="s_type indigo1"><em class="hidden">모집인원</em>1명</span>
          <span class="s_type red1"><em class="hidden">일반접수</em>
          2099-01-01~2099-01-01</span></td>
        <td><span class="s_type2"><em class="hidden">선발식</em></span><span class="s_btn"></span></td>
        <td><a href="javascript:;" onclick="{onclick}"><span>수강신청</span></a></td></tr>
    """


def _platform_page(page: int, *, drift: bool = False, bad_sentinel: bool = False) -> str:
    rows = _rows()
    if drift:
        rows[0] = {**rows[0], "title": "변경된 연계 강좌"}
    body = ""
    if page == 1 or bad_sentinel:
        body = "".join(_platform_row(row, 11 - index) for index, row in enumerate(rows))
        body += _platform_native(1)
    else:
        body = '<tr><td colspan="7">등록된 교육강좌가 없습니다.</td></tr>'
    return f"""
      <html><body><form id="learningVO" method="post" action="{gijang._lifelong.BUSAN_LIFELONG_LIST_PATH}">
        <input name="inst_id" value="{gijang.BUSAN_LIFELONG_GIJANG_OFFICE}">
        <input name="display_type" value="2"><input name="pageIndex" value="{page}">
        <input name="pageUnit" value="950"><input name="l_search_ch" value="0">
        <select id="o_search_ch"><option value="{gijang.BUSAN_LIFELONG_GIJANG_OFFICE}" selected>
          {gijang.BUSAN_LIFELONG_GIJANG_OFFICE_NAME}</option></select>
        <select id="learning_state"><option value="0" selected>전체</option></select>
      </form><table><thead><tr><th>번호</th><th>강좌명 / 교육기관</th><th>재료비 / 강사</th>
        <th>교육기간 / 교육시간</th><th>신청기간 / 접수인원</th><th>상태</th><th>보기</th>
      </tr></thead><tbody>{body}</tbody></table>
      <a class="page_nextend" href="?pageIndex=1" onclick="fn_list(1,'');return false;">마지막</a>
      </body></html>
    """


def _city_page(page: int) -> str:
    return f"""
      <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head><body>
      <form id="srchForm" name="srchForm" method="get" action="/lctre">
        <input name="curPage" value="{page}">
        <select name="srchGugun"><option value="3" selected>기장군</option></select>
        <select name="srchResveInsttCd"><option value="33" selected>주민자치회</option></select>
      </form><div class="reserveListWrap"><div class="txtCenter">등록된 강좌가 없습니다.</div></div>
      <div class="paginate"></div></body></html>
    """


def _fetcher(
    *, local_drift: bool = False, platform_drift: bool = False,
    bad_local_sentinel: bool = False, bad_platform_sentinel: bool = False,
):
    local_one_calls = 0
    platform_one_calls = 0

    def fetch(session: Any, url: str, timeout: int) -> _Response:
        nonlocal local_one_calls, platform_one_calls
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.hostname == gijang.BUSAN_GIJANG_HOST:
            if query.get("mode") == ["view"]:
                identity = query["idx"][0]
                row = next(item for item in _rows() if item["identity"] == identity)
                return _Response(url, _local_detail(row))
            page = int(query.get("pageIndex", ["1"])[0])
            if page == 1:
                local_one_calls += 1
            return _Response(url, _local_page(
                page, drift=local_drift and page == 1 and local_one_calls > 1,
                bad_sentinel=bad_local_sentinel and page == 2,
            ))
        if parsed.hostname == gijang._lifelong.BUSAN_LIFELONG_HOST:
            page = int(query.get("pageIndex", ["1"])[0])
            if page == 1:
                platform_one_calls += 1
            return _Response(url, _platform_page(
                page, drift=platform_drift and page == 1 and platform_one_calls > 1,
                bad_sentinel=bad_platform_sentinel and page == 2,
            ))
        if parsed.hostname == gijang.BUSAN_CITY_HOST:
            return _Response(url, _city_page(int(query.get("curPage", ["1"])[0])))
        raise AssertionError(f"unexpected URL {url}")

    return fetch


def _collect(fetcher=None, **kwargs):
    options = {
        "today": "2099-01-01", "timeout": 3, "max_pages": 10,
        "detail_limit": 10, "max_requests": 30, "max_workers": 1,
    }
    options.update(kwargs)
    return gijang.collect_busan_gijang_education(
        _target(),
        fetcher=fetcher or _fetcher(), session_factory=_Session,
        sleeper=lambda _: None, **options,
    )


def test_target_and_url_helpers_are_fail_closed() -> None:
    assert gijang.is_target(_target())
    assert not gijang.is_target(_target(provider="OTHER"))
    assert not gijang.is_target(_target(url="https://www.gijang.go.kr/lll/index.gijang"))
    assert gijang.canonical_busan_gijang_identity(gijang.busan_gijang_detail_url("10")) == "10"
    assert not gijang.canonical_busan_gijang_identity(gijang.busan_gijang_detail_url("10") + "&extra=1")
    with pytest.raises(gijang.BusanGijangContractError):
        gijang.busan_gijang_detail_url("../10")


def test_atomic_collector_suppresses_platform_duplicates_and_test_native() -> None:
    rows, parser, meta = _collect()
    assert parser == gijang.BUSAN_GIJANG_PARSER
    assert [row["raw_fields"]["source_identity"] for row in rows] == ["10", "9"]
    assert meta["district_source_rows"] == 10
    assert meta["platform_source_rows"] == 11
    assert meta["platform_external_duplicate_rows"] == 10
    assert meta["platform_native_rows"] == 1
    assert meta["platform_excluded_native_non_course_rows"] == 1
    assert meta["city_source_rows"] == 0
    assert meta["returned_count"] == 2
    assert meta["network_requests"] == 16
    assert meta["platform_raw_semantic_censuses"] == 4
    assert meta["platform_reconciled_pairwise_signatures"] == 2
    assert meta["snapshot_complete"] is True
    assert rows[0]["reservation_available"] is True
    assert rows[1]["reservation_available"] is False
    serialized = repr(rows)
    assert "SECRET" not in serialized
    assert "010-1111-2222" not in serialized
    assert "private@example.test" not in serialized


@pytest.mark.parametrize(
    "fetcher",
    [
        _fetcher(local_drift=True), _fetcher(platform_drift=True),
        _fetcher(bad_local_sentinel=True), _fetcher(bad_platform_sentinel=True),
    ],
)
def test_atomic_collector_discards_every_partial_snapshot(fetcher) -> None:
    rows, _, meta = _collect(fetcher=fetcher)
    assert rows == []
    assert meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_caps_fail_closed_before_partial_results() -> None:
    rows, _, meta = _collect(detail_limit=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]


def test_local_detail_allowlist_rejects_title_drift_without_reading_private_values() -> None:
    parent = gijang._parse_local_page(
        gijang.BeautifulSoup(_local_page(1), "lxml"),
        gijang.busan_gijang_list_url(1), page=1,
    )[0][0]
    with pytest.raises(gijang.BusanGijangContractError):
        gijang._parse_local_detail(
            gijang.BeautifulSoup(_local_detail(_rows()[0], wrong_title=True), "lxml"),
            parent["raw_url"], parent,
        )


def test_platform_owner_contract_is_dedicated_and_has_gijang_municipality() -> None:
    office = gijang._platform_office()
    assert office.ownership == "duplicate_dedicated_gijang_owner"
    assert office.municipality_code == "2671000000"
    assert office.municipality_name == "부산광역시 기장군"


def _logical_row(identity: str, sequence: int, title: str | None = None) -> dict[str, Any]:
    return {
        "title": title or identity,
        "start_date": "2099-01-01", "end_date": "2099-01-02",
        "apply_start": "2098-12-01", "apply_end": "2098-12-31",
        "raw_url": gijang.busan_gijang_detail_url(identity),
        "raw_fields": {
            "identity_kind": "external",
            "identity": gijang.busan_gijang_detail_url(identity),
            "list_sequence": sequence,
            "source_status": "마감",
        },
    }


def test_independent_pairwise_unions_repair_only_exact_boundary_duplicates() -> None:
    first, total1, duplicate1 = gijang._platform_raw_census([
        _logical_row("1", 3), _logical_row("1", 2), _logical_row("3", 1),
    ])
    second, total2, duplicate2 = gijang._platform_raw_census([
        _logical_row("1", 3), _logical_row("2", 2), _logical_row("2", 1),
    ])
    third, total3, duplicate3 = gijang._platform_raw_census([
        _logical_row("1", 3), _logical_row("2", 2), _logical_row("3", 1),
    ])
    assert (total1, total2, total3) == (3, 3, 3)
    assert (duplicate1, duplicate2, duplicate3) == (1, 1, 0)
    left, left_signature = gijang._reconcile_platform_pair(
        first, second, declared_total=3
    )
    right, right_signature = gijang._reconcile_platform_pair(
        second, third, declared_total=3
    )
    assert set(left) == set(right) == {
        "external:1", "external:2", "external:3"
    }
    assert left_signature == right_signature


def test_reconciliation_fails_closed_on_incomplete_or_conflicting_union() -> None:
    first, _, _ = gijang._platform_raw_census([
        _logical_row("1", 3), _logical_row("1", 2), _logical_row("3", 1),
    ])
    still_missing, _, _ = gijang._platform_raw_census([
        _logical_row("1", 3), _logical_row("1", 2), _logical_row("3", 1),
    ])
    with pytest.raises(gijang.BusanGijangContractError, match="does not repair"):
        gijang._reconcile_platform_pair(first, still_missing, declared_total=3)
    conflict, _, _ = gijang._platform_raw_census([
        _logical_row("1", 3, title="changed"),
        _logical_row("2", 2),
        _logical_row("3", 1),
    ])
    with pytest.raises(gijang.BusanGijangContractError, match="semantics changed"):
        gijang._reconcile_platform_pair(first, conflict, declared_total=3)


def test_managed_session_scopes_only_timeout_and_keeps_default_body_cap() -> None:
    session = gijang.busan_gijang_session_factory()
    try:
        assert session.max_response_bytes == gijang.DEFAULT_MAX_RESPONSE_BYTES
        assert gijang.BUSAN_GIJANG_MAX_HTML_BYTES == gijang.DEFAULT_MAX_RESPONSE_BYTES
        assert session.total_timeout_seconds == 120
    finally:
        session.close()


@pytest.mark.skipif(
    os.getenv("RUN_BUSAN_GIJANG_LIVE_TEST") != "1",
    reason="set RUN_BUSAN_GIJANG_LIVE_TEST=1 for the exact live census",
)
def test_live_exact_gijang_snapshot() -> None:
    rows, parser, meta = gijang.collect_busan_gijang_education(
        _target(), today="2026-07-22", timeout=35, max_pages=220,
        detail_limit=60, max_requests=260, max_workers=20,
        session_factory=gijang.busan_gijang_session_factory,
    )
    assert parser == gijang.BUSAN_GIJANG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["district_source_rows"] == 1690
    assert meta["district_data_pages"] == 169
    assert meta["district_unique_ids"] == 1690
    assert meta["district_status_counts"] == {"교육완료": 1678, "접수중": 7, "접수마감": 5}
    assert meta["district_reversed_education_period_rows"] == 1
    assert meta["district_reversed_application_period_rows"] == 4
    assert meta["district_current_count"] == 12
    assert meta["district_publishable_current_count"] == 10
    assert meta["platform_source_rows"] == 1687
    assert meta["platform_external_duplicate_rows"] == 1686
    assert meta["platform_external_unique_idx"] == 1686
    assert meta["platform_native_rows"] == 1
    assert meta["platform_excluded_native_non_course_rows"] == 1
    assert meta["city_source_rows"] == 0
    assert meta["platform_data_pages"] == 2
    assert meta["platform_page_units"] == [900, 950, 900, 950]
    assert meta["platform_raw_semantic_censuses"] == 4
    assert meta["platform_raw_census_row_counts"] == [1687, 1687, 1687, 1687]
    assert meta["platform_reconciled_pairwise_signatures"] == 2
    assert meta["platform_reconciled_identity_count"] == 1687
    assert meta["network_requests"] == 197
    assert len(rows) == 10
    assert meta["snapshot_complete"] is True
