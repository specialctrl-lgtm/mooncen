from __future__ import annotations

from datetime import date
from html import escape
import os
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import municipal_busan_bukgu as bukgu


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _target(
    provider: str = bukgu.BUSAN_BUKGU_PROVIDER,
    url: str = bukgu.BUSAN_BUKGU_CANONICAL_URL,
) -> dict[str, str]:
    return {"provider": provider, "url": url, "name": "부산광역시 북구"}


def _local_form(ledger: bukgu._LocalLedger, *, partition: str = "") -> str:
    method = "get" if ledger is bukgu._LIBRARY else "post"
    hidden = (
        f'<input type="hidden" name="menuCd" value="{ledger.menu}">'
        if ledger is bukgu._LIBRARY
        else ""
    )
    options = [('<option value="">', "접수상태")]
    for value, label in (
        ("ing", "접수중"),
        ("wait", "접수대기"),
        ("close", "접수종료"),
    ):
        selected = " selected" if value == partition else ""
        options.append((f'<option value="{value}"{selected}>', label))
    rendered_options = "".join(f"{start}{label}</option>" for start, label in options)
    return f"""
      <div class="search"><form method="{method}"
        action="/reservation/index.bsbukgu?menuCd={ledger.menu}&amp;mode=list">
        {hidden}<select name="registerStatus">{rendered_options}</select>
      </form></div>
    """


def _local_card(
    ledger: bukgu._LocalLedger,
    *,
    identity: str,
    page: int,
    title: str = "미래 강좌",
    status: str = "접수중",
    bad_href: bool = False,
) -> str:
    if ledger is bukgu._INFORMATION:
        href = (
            f"?menuCd={ledger.menu}&amp;page={page}&amp;mode=view&amp;"
            f"lectureIdx={identity}"
        )
        values = {
            "교육기간": "2099-02-01 ~ 2099-02-28",
            "접수인원": "0",
            "대기인원": "0",
            "모집인원": "20",
        }
    elif ledger is bukgu._LIFELONG:
        href = f"?menuCd={ledger.menu}&amp;mode=view&amp;programIdx={identity}"
        values = {
            "신청기간": "2099-01-01 ~ 2099-01-31",
            "교육기간": "2099-02-01 ~ 2099-02-28",
            "온라인접수": "10",
            "전화접수": "0",
            "방문접수": "0",
            "교육대상": "북구민",
        }
    else:
        tail = f"page={page}" if ledger is bukgu._LIBRARY else str(page)
        href = (
            f"?menuCd={ledger.menu}&amp;mode=view&amp;programIdx={identity}"
            f"&amp;{tail}"
        )
        values = {
            "신청기간": "2099-01-01 ~ 2099-01-31",
            "수강일자": "2099-02-01 ~ 2099-02-28 (10:00~12:00)",
            "정원/현재원": "20 / 0",
            "강의장소": "덕천도서관",
        }
    if bad_href:
        href = "https://evil.example/reservation/index.bsbukgu?" + href.split("?", 1)[-1]
    fields = "".join(
        f"<p><strong>{escape(label)}</strong><span>{escape(value)}</span></p>"
        for label, value in values.items()
    )
    library_name = "<span class='lib_name'>(덕천)</span>" if ledger in {
        bukgu._LIBRARY,
        bukgu._SMALL_LIBRARY,
    } else ""
    return f"""
      <li><a href="{href}"><span class="btxt">{library_name}
        <span class="tit">{escape(title)}</span>
        <span class="state">{status}</span></span>
        <span class="inlec">{fields}</span></a></li>
    """


def _local_page(
    ledger: bukgu._LocalLedger,
    *,
    page: int = 1,
    total: int = 1,
    partition: str = "",
    cards: str = "",
    current_marker: bool = True,
) -> BeautifulSoup:
    marker = f"<strong>{page}</strong>" if current_marker else ""
    return _soup(
        f"""
        <html><head><title>교육/강좌 &lt; {ledger.name}</title></head>
        <body><div class="board-top"><div class="total">
          총 <span>{total}</span>건의 게시물이 있습니다</div>
          {_local_form(ledger, partition=partition)}</div>
          <div class="courseList-wrap"><ul>{cards}</ul></div>
          <div class="pageing">{marker}</div>
        </body></html>
        """
    )


def _library_parent(*, identity: str = "9001") -> dict[str, object]:
    return {
        "title": "미래 도서관 강좌",
        "raw_url": bukgu.busan_bukgu_detail_url(bukgu._LIBRARY, identity),
        "start_date": "2099-02-01",
        "end_date": "2099-02-28",
        "apply_start": "2099-01-01",
        "apply_end": "2099-01-31",
        "venue_name": "덕천도서관",
        "branch": "덕천도서관",
        "raw_fields": {
            "source_ledger": "library",
            "source_identity": identity,
            "source_status": "접수중",
            "source_application_period": "2099-01-01 ~ 2099-01-31",
        },
    }


def _library_detail(
    *,
    identity: str = "9001",
    command: str = "insert",
    application_href: str = "",
) -> BeautifulSoup:
    safe = {
        "수강기간": "2099-02-01 ~ 2099-02-28",
        "수강일시": "토 10:00~12:00",
        "장소": "덕천도서관",
        "신청상태": "접수중",
        "신청기간": "2099-01-01 ~ 2099-01-31",
        "수강료": "무료",
    }
    skipped = {
        "정원/현재원": "SECRET_COUNT 010-1111-2222",
        "추가인원": "SECRET_WAITLIST",
        "강사명": "SECRET_INSTRUCTOR private@example.test",
        "강의계획서": "SECRET_PLAN.pdf",
        "비고": "SECRET_NOTE",
    }
    rows = "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>"
        for label, value in {**safe, **skipped}.items()
    )
    href = application_href or (
        f"?menuCd={bukgu._LIBRARY.menu}&amp;mode=form&amp;"
        f"programIdx={identity}&amp;command={command}"
    )
    return _soup(
        f"""
        <html><head><title>교육/강좌 &lt; {bukgu._LIBRARY.name}</title></head>
        <body><div id="conts"><div class="tbl_wrap"><table class="tbl">
          <thead><tr><th>미래 도서관 강좌</th></tr></thead>
          <tbody>{rows}<tr><td>SECRET_FREE_FORM 010-9999-9999</td></tr></tbody>
        </table></div><div class="taC mg30t"><a class="btn done"
          href="{href}">
          프로그램신청</a></div></div></body></html>
        """
    )


def _city_card(
    *,
    branch: str = "북구 화명1동 주민자치회",
    status: str = "대기중",
) -> str:
    values = (
        ("기관", branch),
        ("대상", "제한없음"),
        ("장소", "-"),
        (
            "일자",
            "[신청] 2099-07-01 ~ 2099-07-31 "
            "[행사] 2099-08-01 ~ 2099-08-31",
        ),
        ("방법", "방문접수, 전화접수"),
        ("문의", "SECRET_CITY_CARD_PHONE 051-800-9999"),
    )
    definitions = "".join(
        f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>"
        for label, value in values
    )
    return f"""
      <li><a class="reserveItem" href="javascript:void(0);"
        onclick="fn_viewProgrm('219', '25041');return false;">
        <div class="infoBox">
          <p class="tit" title="주민센터 목공교실">주민센터 목공교실</p>
          <span class="statusMark possible">{status}</span>
          <dl>{definitions}</dl>
        </div>
      </a></li>
    """


def _city_page(
    *,
    page: int,
    cards: str = "",
    last: int = 1,
) -> BeautifulSoup:
    reserve_list = f'<ul class="reserveList">{cards}</ul>' if page <= last else ""
    empty = (
        '<div class="txtCenter">등록된 강좌가 없습니다.</div>'
        if page == last + 1
        else ""
    )
    return _soup(
        f"""
        <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head>
        <body>
          <form id="srchForm" name="srchForm" method="get" action="/lctre">
            <input name="curPage" value="{page}">
            <select name="srchGugun">
              <option value="8" selected>북구</option>
            </select>
            <select name="srchResveInsttCd">
              <option value="33" selected>주민자치회</option>
            </select>
          </form>
          {reserve_list}{empty}
          <div class="paginate"><a class="pgEnd"
            href="?curPage={last}&amp;srchGugun=8&amp;srchResveInsttCd=33">
            마지막</a></div>
        </body></html>
        """
    )


def _city_detail(*, status: str = "대기중") -> BeautifulSoup:
    values = (
        ("운영기간", "2099-08-01(토) ~ 2099-08-31(월)"),
        ("신청기간", "2099-07-01(수) 09:00 ~ 2099-07-31(금) 18:00"),
        ("취소여부", "취소 가능"),
        ("신청방법", "방문접수, 전화접수"),
        ("수강료", "20,000 원"),
        ("요일 /시간", "수 / 14:00 ~ 16:00"),
        ("문의전화", "SECRET_CITY_DETAIL_PHONE 051-800-8888"),
        ("운영기관", "북구 화명1동 주민자치회"),
        ("대상", "제한없음"),
    )
    definitions = "".join(
        f"<dl><dt>{escape(label)}</dt><dd>{escape(value)}</dd></dl>"
        for label, value in values
    )
    return _soup(
        f"""
        <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head>
        <body><form id="viewForm" method="post">
          <input name="resveGroupSn" value="219">
          <input name="progrmSn" value="25041">
          <div class="contHeader"><h3 class="titPage">주민센터 목공교실
            <span class="statusMark possible">{status}</span>
          </h3></div>
          <div class="reserveStateWrap">
            <div class="reserveStateInfo">{definitions}</div>
            <div class="reserveBtnWrap">
              <a class="btnTypeXL" href="/lctre/list">목록</a>
            </div>
          </div>
          <div class="reserveDetail">
            SECRET_CITY_FREE_FORM city@example.test
          </div>
        </form></body></html>
        """
    )


def test_canonical_owner_and_audit_constants() -> None:
    assert bukgu.BUSAN_BUKGU_MUNICIPALITY_CODE == "2632000000"
    assert bukgu.BUSAN_BUKGU_MUNICIPALITY_NAME == "부산광역시 북구"
    assert bukgu.BUSAN_BUKGU_CANDIDATE_IDS["canonical_integrated_reservation"] == (
        "MUNI_IR_8A228D1C0236"
    )
    audit = bukgu.BUSAN_BUKGU_DISCOVERY_AUDIT
    assert audit["lifelong_declared_rows"] == 1406
    assert audit["lifelong_status_union_rows"] == 1387
    assert audit["lifelong_declared_unrendered_rows"] == 19
    assert audit["library_rows"] == 4345
    assert audit["library_advertised_last_page"] == 218
    assert audit["library_actual_data_pages"] == 223
    assert audit["small_library_rows"] == 31
    assert audit["small_library_identity_overlap_rows"] == 0
    assert audit["library_list_projected_current_rows"] == 44
    assert audit["library_current_rows"] == 54
    assert audit["small_library_current_rows"] == 3
    assert audit["busan_city_resident_rows"] == 1
    assert audit["atomic_current_rows"] == 79
    assert {
        bukgu._platform_office(code).ownership
        for code in bukgu.BUSAN_LIFELONG_BUKGU_OFFICES
    } == {"duplicate_dedicated_bukgu_owner"}


@pytest.mark.parametrize(
    "provider,url",
    [
        ("wrong", bukgu.BUSAN_BUKGU_URL),
        (bukgu.BUSAN_BUKGU_PROVIDER, "http://www.bsbukgu.go.kr/reservation/index.bsbukgu"),
        (bukgu.BUSAN_BUKGU_PROVIDER, bukgu.BUSAN_BUKGU_URL + "?menuCd=x"),
        (bukgu.BUSAN_BUKGU_PROVIDER, "https://evil.example/reservation/index.bsbukgu"),
        (bukgu.BUSAN_BUKGU_PROVIDER, bukgu.BUSAN_BUKGU_URL + "#fragment"),
    ],
)
def test_target_rejects_aliases_and_scope_changes(provider: str, url: str) -> None:
    assert not bukgu.is_busan_bukgu_education_target(_target(provider, url))


def test_target_and_url_builders_are_identity_bound() -> None:
    assert bukgu.is_target(_target())
    list_url = bukgu.busan_bukgu_list_url(bukgu._LIFELONG, 3, register_status="ing")
    assert parse_qs(urlparse(list_url).query) == {
        "menuCd": [bukgu._LIFELONG.menu],
        "registerStatus": ["ing"],
        "page": ["3"],
    }
    platform = bukgu.busan_bukgu_lifelong_list_url("OFFICE_00002650", 1)
    assert parse_qs(urlparse(platform).query)["pageUnit"] == ["1000"]
    with pytest.raises(bukgu.BusanBukguContractError):
        bukgu.busan_bukgu_lifelong_list_url("OFFICE_EVIL", 1)


def test_external_identity_is_canonical_only_for_exact_lifelong_detail() -> None:
    good = bukgu.busan_bukgu_detail_url(bukgu._LIFELONG, "2142")
    assert bukgu.canonical_busan_bukgu_course_identity(good) == "2142"
    assert not bukgu.canonical_busan_bukgu_course_identity(good + "&page=1")
    assert not bukgu.canonical_busan_bukgu_course_identity(good.replace("https://", "http://"))


def test_external_projection_drift_is_auditable_without_changing_owner() -> None:
    owner = {
        "title": "최신 강좌명",
        "start_date": "2099-08-01",
        "end_date": "2099-08-31",
        "apply_start": "2099-07-01",
        "apply_end": "2099-07-31",
    }
    stale_projection = {**owner, "title": "이전 강좌명", "apply_end": "2099-07-30"}
    assert bukgu._course_identity_field_mismatches(
        stale_projection, owner
    ) == ("title", "apply_end")
    assert not bukgu._same_course_identity_fields(stale_projection, owner)


def test_library_list_parser_keeps_only_allowlisted_fields() -> None:
    card = _local_card(bukgu._LIBRARY, identity="9001", page=1)
    rows, total, last = bukgu._parse_local_page(
        _local_page(bukgu._LIBRARY, cards=card),
        ledger=bukgu._LIBRARY,
        page=1,
        cutoff=date(2026, 7, 22),
    )
    assert (len(rows), total, last) == (1, 1, 1)
    assert rows[0]["provider_course_id"].endswith(":library:9001")
    assert rows[0]["start_date"] == "2099-02-01"
    assert rows[0]["application_url"] == ""
    assert rows[0]["reservation_available"] is False
    assert rows[0]["raw_fields"]["application_form_fetched"] is False


def test_local_parser_rejects_cross_host_detail_and_wrong_partition() -> None:
    bad = _local_card(bukgu._LIBRARY, identity="9001", page=1, bad_href=True)
    with pytest.raises(bukgu.BusanBukguContractError, match="unsafe"):
        bukgu._parse_local_page(
            _local_page(bukgu._LIBRARY, cards=bad),
            ledger=bukgu._LIBRARY,
            page=1,
            cutoff=date(2026, 7, 22),
        )
    closed = _local_card(
        bukgu._LIFELONG,
        identity="9002",
        page=1,
        status="교육종료",
    )
    with pytest.raises(bukgu.BusanBukguContractError, match="title/status"):
        bukgu._parse_local_page(
            _local_page(bukgu._LIFELONG, cards=closed, partition="ing"),
            ledger=bukgu._LIFELONG,
            page=1,
            cutoff=date(2026, 7, 22),
            register_status="ing",
        )


def test_lifelong_waitlist_status_is_current_in_ing_partition() -> None:
    card = _local_card(
        bukgu._LIFELONG,
        identity="2717",
        page=1,
        status="대기접수중",
    )
    rows, total, last = bukgu._parse_local_page(
        _local_page(
            bukgu._LIFELONG,
            cards=card,
            partition="ing",
        ),
        ledger=bukgu._LIFELONG,
        page=1,
        cutoff=date(2026, 7, 29),
        register_status="ing",
    )
    assert (len(rows), total, last) == (1, 1, 1)
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["raw_fields"]["source_status"] == "대기접수중"


def test_last_page_active_marker_and_post_last_sentinel_are_accepted() -> None:
    page2 = _local_page(
        bukgu._SMALL_LIBRARY,
        page=2,
        total=31,
        current_marker=True,
    )
    rows, total, last = bukgu._parse_local_page(
        page2,
        ledger=bukgu._SMALL_LIBRARY,
        page=2,
        cutoff=date(2026, 7, 22),
    )
    assert rows == []
    assert (total, last) == (31, 2)


def test_only_exact_audited_date_defects_are_normalized() -> None:
    start, end, historical, corrected = bukgu._library_course_range(
        "2017-05-02 ~ 2017-04-19 (10:00 ~ 12:00 (10일 과정))",
        identity="502139",
        source_status="접수종료",
        cutoff=date(2026, 7, 22),
    )
    assert (start, end, historical, corrected) == (
        "2017-04-19",
        "2017-05-02",
        False,
        True,
    )
    with pytest.raises(bukgu.BusanBukguContractError, match="new reversed"):
        bukgu._library_course_range(
            "2099-03-01 ~ 2099-02-01",
            identity="9999",
            source_status="접수중",
            cutoff=date(2026, 7, 22),
        )
    assert bukgu._strict_range(
        "2026-06-30 ~ 2206-07-24",
        ledger=bukgu._LIBRARY,
        identity="1104574",
        kind="application",
    ) == ("2026-06-30", "2026-07-24", True)


def test_library_detail_is_allowlisted_and_application_control_is_bound() -> None:
    parent = _library_parent()
    result = bukgu._parse_local_detail(
        _library_detail(), str(parent["raw_url"]), parent
    )
    serialized = repr(result)
    assert "SECRET_" not in serialized
    assert "010-" not in serialized
    assert "example.test" not in serialized
    assert result["application_url"] == parent["raw_url"]
    assert result["application_type"] == "ONLINE_RESERVATION"
    assert result["reservation_available"] is True
    assert result["target"] == "대상 별도 안내"
    assert result["raw_fields"]["target_evidence"] == (
        "official_library_detail_omits_target"
    )
    assert result["raw_fields"]["application_form_fetched"] is False
    with pytest.raises(bukgu.BusanBukguContractError, match="application control"):
        bukgu._parse_local_detail(
            _library_detail(command="delete"), str(parent["raw_url"]), parent
        )


def test_library_detail_expands_first_session_projection() -> None:
    parent = _library_parent()
    parent["end_date"] = "2099-02-01"
    parent["period"] = "2099-02-01 ~ 2099-02-01"
    result = bukgu._parse_local_detail(
        _library_detail(), str(parent["raw_url"]), parent
    )
    assert result["end_date"] == "2099-02-28"
    assert result["period"] == "2099-02-01 ~ 2099-02-28"
    assert result["raw_fields"]["list_course_date_projection"] is True


def test_exact_unbound_library_application_is_blocked_not_exposed() -> None:
    parent = _library_parent(identity="1102558")
    detail = _library_detail(
        identity="1102558",
        application_href=bukgu._AUDITED_UNBOUND_LIBRARY_APPLICATION["url"],
    )
    result = bukgu._parse_local_detail(detail, str(parent["raw_url"]), parent)
    assert result["reservation_available"] is False
    assert result["application_url"] == ""
    assert result["application_type"] == "INFO_ONLY"
    assert result["raw_fields"]["unbound_application_control_blocked"] is True


def test_city_complete_partition_detail_and_sentinel_are_exact() -> None:
    rows, last = bukgu._parse_city_page(
        _city_page(page=1, cards=_city_card()),
        page=1,
    )
    assert (len(rows), last) == (1, 1)
    parent = rows[0]
    assert parent["venue_name"] == "북구 화명1동 주민자치회"
    assert parent["raw_fields"]["source_venue"] == "-"
    assert parent["raw_fields"]["venue_fallback_used"] is True
    sentinel, sentinel_last = bukgu._parse_city_page(
        _city_page(page=2),
        page=2,
        expected_last=1,
    )
    assert sentinel == []
    assert sentinel_last == 1

    result = bukgu._parse_city_detail(
        _city_detail(),
        bukgu.busan_bukgu_city_detail_url("219", "25041"),
        parent,
    )
    assert result["fee"] == "20,000 원"
    assert result["schedule_raw"] == "수 / 14:00 ~ 16:00"
    assert result["target"] == "제한없음"
    assert result["application_type"] == "INFO_ONLY"
    assert result["raw_fields"]["inquiry_value_never_read"] is True
    serialized = repr(result)
    assert "SECRET_CITY" not in serialized
    assert "051-800-" not in serialized
    assert "example.test" not in serialized

    with pytest.raises(bukgu.BusanBukguContractError, match="left Buk-gu"):
        bukgu._parse_city_page(
            _city_page(
                page=1,
                cards=_city_card(branch="서구 다른동 주민자치회"),
            ),
            page=1,
        )


def test_recursive_privacy_sanitizer_removes_keys_phones_and_emails() -> None:
    safe, redactions = bukgu._sanitize_row(
        {
            "title": "safe",
            "phone": "010-1111-2222",
            "nested": {
                "teacher_name": "SECRET",
                "note": "call 051-123-4567 or private@example.test",
            },
        }
    )
    assert redactions == 4
    assert safe == {"title": "safe", "nested": {"note": "call or"}}


def test_collector_rejects_wrong_target_and_insufficient_caps_without_network() -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("network must not be called")

    rows, parser, meta = bukgu.collect_busan_bukgu_education(
        _target(provider="wrong"), fetcher=forbidden
    )
    assert rows == [] and parser == bukgu.BUSAN_BUKGU_PARSER
    assert meta["configured_collection_error"]
    rows, _, meta = bukgu.collect_busan_bukgu_education(
        _target(), max_pages=9, fetcher=forbidden
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "caps" in meta["configured_collection_error"]


def test_collector_is_atomic_on_first_page_contract_failure() -> None:
    calls = 0

    def broken_fetcher(session: object, url: str, timeout: int) -> tuple[BeautifulSoup, str]:
        nonlocal calls
        calls += 1
        return _soup("<html><head><title>changed</title></head></html>"), url

    rows, _, meta = bukgu.collect_busan_bukgu_education(
        _target(), fetcher=broken_fetcher, sleeper=lambda _: None
    )
    assert calls >= 10
    assert rows == []
    assert meta["returned_count"] == 0
    assert meta["pagination_complete"] is False
    assert meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("MOONCEN_RUN_LIVE_CRAWLER_TESTS") != "1",
    reason="set MOONCEN_RUN_LIVE_CRAWLER_TESTS=1 for the exact live census",
)
def test_exact_live_busan_bukgu_snapshot() -> None:
    rows, parser, meta = bukgu.collect_busan_bukgu_education(
        _target(),
        today="2026-07-22",
        timeout=45,
        max_pages=450,
        detail_limit=300,
        max_requests=700,
        max_workers=8,
    )
    assert parser == bukgu.BUSAN_BUKGU_PARSER
    assert meta["configured_collection_error"] == ""
    assert len(rows) == meta["returned_count"] == 87
    assert meta["lifelong_declared_rows"] == 1406
    assert meta["lifelong_default_rendered_rows"] == 1380
    assert meta["lifelong_status_union_rows"] == 1387
    assert meta["lifelong_declared_unrendered_rows"] == 19
    assert meta["lifelong_status_partition_recovered_rows"] == 7
    assert meta["library_source_rows"] == 4345
    assert meta["library_advertised_last_page"] == 218
    assert meta["library_actual_data_pages"] == 223
    assert meta["small_library_independent_rows"] == 31
    assert meta["small_library_identity_overlap_rows"] == 0
    assert meta["library_list_projected_current_rows"] == 44
    assert meta["library_current_year_detail_probe_rows"] == 215
    assert meta["library_projection_recovered_current_rows"] == 15
    assert meta["library_current_count"] == 59
    assert meta["small_library_current_year_detail_probe_rows"] == 9
    assert meta["small_library_projection_recovered_current_rows"] == 2
    assert meta["small_library_current_count"] == 2
    assert meta["district_current_count"] == 79
    assert meta["platform_native_current_count"] == 8
    assert meta["detail_attempts"] == 250
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
