from __future__ import annotations

from collections import Counter
from copy import deepcopy
import ssl
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_ansan as ansan


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def test_required_source_omissions_are_explicit_and_auditable() -> None:
    rows = [
        {
            "title": "공식 필드 누락 강좌",
            "target": "",
            "schedule_raw": " ",
            "raw_fields": {},
        }
    ]

    ansan._mark_required_source_omissions(rows)

    assert rows[0]["target"] == "공식 페이지 미기재"
    assert rows[0]["schedule_raw"] == "공식 페이지 시간 미기재"
    assert rows[0]["raw_fields"]["target_source_omission"] is True
    assert rows[0]["raw_fields"]["schedule_source_omission"] is True


def _lll_card(
    catalogue: ansan.AnsanLifelongCatalogue,
    *,
    identity: str = "NOREDU_12345678",
    title: str = "안산 시민 글쓰기",
    status: str = "교육접수중",
    start: str = "2099-07-01",
    end: str = "2099-08-31",
    day: str = "월요일",
    time: str = "10:00 ~ 12:00",
    venue: str = "안산시평생학습관",
    linked: bool = True,
    auxiliary_badge: str = "",
    application: bool = True,
) -> str:
    href = f"javascript:fn_go_detail('{identity}')" if linked else "#none"
    link_extra = "" if linked else " class='line-through' onclick='return false;'"
    control = (
        f"<a class='btn_apply' href=\"javascript:fn_go_reply('{identity}')\">수강신청</a>"
        if application
        else ""
    )
    badge = f"<span class='cate_bg'>{auxiliary_badge}</span>" if auxiliary_badge else ""
    return f"""
      <div class="board_section">
        <div class="cate">{badge}<span class="cate_border">{status}</span></div>
        <div class="info"><div class="tp"><a href="{href}"{link_extra}>{title}</a></div></div>
        <ul class="bm">
          <li><strong>교육기간 :</strong><span class="txt">{start} ~ {end}</span></li>
          <li><strong>수강일 :</strong><span class="txt">{day}</span></li>
          <li><strong>시간 :</strong><span class="txt">{time}</span></li>
          <li><strong>수강대상자 :</strong><span class="txt">안산시민</span></li>
          <li><strong>장소 :</strong><span class="txt">{venue}</span></li>
        </ul>
        <ul class="edu_status">
          <li><span class="f">신청</span><span class="txt">3명</span></li>
          <li><span class="f">정원</span><span class="txt">20명</span></li>
        </ul>{control}
      </div>
    """


def _lll_page(
    catalogue: ansan.AnsanLifelongCatalogue,
    *,
    cards: str,
    total: int,
) -> str:
    directory = ""
    if catalogue.code == "nor":
        paths = [item.list_path for item in ansan.ANSAN_LIFELONG_CATALOGUES]
        paths.append("/web/cop/lectEduList.do")
        directory = "".join(f"<a href='{path}'>메뉴</a>" for path in paths)
    return (
        f"<html><body>{directory}<div>전체 : {total}건</div>"
        f"<a href=\"javascript:linkPage(1)\">1</a>"
        f"<div class='list-board'>{cards}</div></body></html>"
    )


def _lll_detail(
    *,
    identity: str = "NOREDU_12345678",
    title: str = "안산 시민 글쓰기",
    status: str = "교육접수중",
    start: str = "2099-07-01",
    end: str = "2099-08-31",
    application: bool = True,
) -> str:
    control = (
        f"<a href=\"javascript:fn_go_reply('{identity}')\">수강신청</a>"
        if application
        else ""
    )
    return f"""
      <html><body>
        <div class="board_section">
          <div class="cate"><span class="cate_border">{status}</span></div>
          <div class="info"><div class="tp"><h4>{title}</h4></div></div>
          <ul class="bm"><li><strong>교육기간 :</strong>
            <span class="txt">{start} ~ {end}</span></li></ul>
        </div>
        <section><h4 class="tit">강의 기본정보</h4>
          <div class="board_write">
            <div class="row"><div class="div_th">교육기간</div>
              <div class="div_td">{start} ~ {end}</div></div>
            <div class="row"><div class="div_th">신청기간</div>
              <div class="div_td">2099-06-01 ~ 2099-06-30</div></div>
            <div class="row"><div class="div_th">교육대상</div><div class="div_td">안산시민</div></div>
            <div class="row"><div class="div_th">강의장</div><div class="div_td">101호</div></div>
            <div class="row"><div class="div_th">수강료</div><div class="div_td">무료</div></div>
          </div>
        </section>{control}
        <footer>경기도 안산시 상록구 차돌배기로 24-1</footer>
      </body></html>
    """


def _reserve_options() -> str:
    return "".join(
        f"<option value='{code}'>{name}</option>"
        for code, name in [("all", "전체")]
        + [(item.code, item.name) for item in ansan.ANSAN_RESERVE_CATEGORIES]
    )


def _reserve_card(
    *,
    identity: str = "RESR_000000000019999",
    link_yn: str = "N",
    title: str = "단원 청소년 코딩",
    status: str = "접수중",
    start: str = "2099-07-01",
    end: str = "2099-08-31",
    day: str = "화요일",
    time: str = "14:00 ~ 16:00",
    branch: str = "단원청소년수련관",
) -> str:
    return f"""
      <li><a href="#none" onclick="fnView('{identity}','{link_yn}')">상세</a>
        <span class="label">{status}</span>
        <div class="txtW"><span class="tit">{title}</span><ul class="etc">
          <li><span class="em">기관/부서</span><span class="txt">{branch}</span></li>
          <li><span class="em">교육기간</span><span class="txt">{start} ~ {end}</span></li>
          <li><span class="em">접수기간</span><span class="txt">2099-06-01 ~ 2099-06-30</span></li>
          <li><span class="em">요일</span><span class="txt">{day}</span></li>
          <li><span class="em">교육시간</span><span class="txt">{time}</span></li>
          <li><span class="em">대상</span><span class="txt">청소년</span></li>
          <li><span class="em">사용료</span><span class="txt">무료</span></li>
          <li><span class="em">위치</span><span class="txt">교육실</span></li>
        </ul></div>
      </li>
    """


def _reserve_page(*, cards: str, total: int) -> str:
    return f"""
      <html><body><select name="searchClsfCd">{_reserve_options()}</select>
        <div>전체 : {total}건</div><a href="javascript:fnSearch(1)">1</a>
        <ul class="blog reserv">{cards}</ul></body></html>
    """


def _reserve_detail(
    *,
    identity: str = "RESR_000000000019999",
    link_yn: str = "N",
    title: str = "단원 청소년 코딩",
    status: str = "접수중",
    period_label: str = "교육기간",
    start: str = "2099-07-01",
    end: str = "2099-08-31",
    branch: str = "단원청소년수련관",
    capacity: str = "3 / 20명",
    control: str = "대기신청",
    control_onclick: str = "checkInTracer();",
) -> str:
    favorite = f"<button onclick=\"fnFavorite('{identity}')\">찜</button>" if link_yn == "N" else ""
    if control and link_yn == "N":
        application = (
            f"<a id='resvRqstBtn' class='btn block waiting' href='#none' "
            f"onclick='{control_onclick}'>{control}</a>"
        )
    elif control:
        application = (
            "<a href='#this' onclick=\"fnCmbResvView('https://apply.ansan.go.kr/course')\">"
            "예약신청</a>"
        )
    else:
        application = ""
    return f"""
      <html><body>{favorite}<div class="listInfo"><div class="infoArea">
        <span class="tit">{title}</span><span class="label">{status}</span>
        <ul class="itemList">
          <li><span class="em">기관/부서</span><span class="txt">{branch}</span></li>
          <li><span class="em">{period_label}</span><span class="txt">{start} ~ {end}</span></li>
          <li><span class="em">접수기간</span><span class="txt">2099-06-01 ~ 2099-06-30</span></li>
          <li><span class="em">요일</span><span class="txt">화요일</span></li>
          <li><span class="em">교육시간</span><span class="txt">14:00 ~ 16:00</span></li>
          <li><span class="em">대상</span><span class="txt">청소년</span></li>
          <li><span class="em">사용료</span><span class="txt">무료</span></li>
          <li><span class="em">모집정원</span><span class="txt">{capacity}</span></li>
          <li><span class="em">시설명</span><span class="txt">교육실</span></li>
        </ul>{application}
      </div></div>
      <div class="rsvPlace"><ul class="loca"><li><span class="em">위치</span>
        경기도 안산시 단원구 중앙대로 1</li></ul></div>
      </body></html>
    """


def _reserve_unpublished_shell(
    message: str = "존재하지 않는 교육/강좌입니다.",
) -> str:
    return f"""
      <html><head><title>안산시 통합예약시스템</title></head><body>
        <script>alert('{message}');</script>
        <footer>문의전화 1666-1234 경기도 안산시 단원구 화랑로 387(고잔동)</footer>
      </body></html>
    """


def _road_page(*, card: bool) -> str:
    body = ""
    if card:
        body = """
          <div class="board_section board_single map_view">No. 1
            <div class="info"><div class="tp"><a>단원 길거리학습관</a></div></div>
            <ul class="bm"><li><strong>주소 :</strong>
              <span class="txt">경기도 안산시 단원구 중앙대로 1</span></li></ul>
          </div>
        """
    return f"<html><body><a href='javascript:linkPage(1)'>1</a>{body}</body></html>"


def test_exact_owner_codes_urls_aliases_and_legacy_tls() -> None:
    target = {"provider": ansan.ANSAN_PROVIDER, "url": ansan.ANSAN_CANONICAL_URL}
    assert ansan.is_ansan_education_target(target)
    assert not ansan.is_ansan_education_target({**target, "url": target["url"] + "?page=1"})
    assert not ansan.is_ansan_education_target({**target, "url": "https://lll.ansan.go.kr.evil.test/web/cop/norEduList.do"})
    assert not ansan.is_ansan_education_target({**target, "provider": "OTHER"})
    assert (ansan.ANSAN_CITY_CODE, ansan.ANSAN_SANGNOK_CODE, ansan.ANSAN_DANWON_CODE) == (
        "4127000000",
        "4127100000",
        "4127300000",
    )
    assert "4115000000" not in {item["code"] for item in ansan.ANSAN_COVERED_MUNICIPALITIES}
    assert len(ansan.ANSAN_LIFELONG_CATALOGUES) == 4
    assert len(ansan.ANSAN_RESERVE_CATEGORIES) == 7
    assert {item.ownership for item in ansan.ANSAN_NON_EXECUTING_ALIASES} == {
        "lifelong_catalogue_subset",
        "reserve_catalogue_component",
        "navigation_shell",
        "reserve_category_subset",
    }

    native = parse_qs(urlparse(ansan.ansan_reserve_detail_url(ansan.ANSAN_RESERVE_CATEGORIES[0], "RESR_000000000019999")).query)
    legacy = parse_qs(urlparse(ansan.ansan_reserve_detail_url(ansan.ANSAN_RESERVE_CATEGORIES[0], "25784269")).query)
    assert native["resrId"] == ["RESR_000000000019999"] and "linkYn" not in native
    assert legacy["resrId"] == ["25784269"] and legacy["linkYn"] == ["Y"]
    with pytest.raises(ValueError):
        ansan.ansan_reserve_detail_url(ansan.ANSAN_RESERVE_CATEGORIES[0], "25784269", "N")

    session = ansan.ansan_session_factory()
    try:
        assert session.verify is True
        adapter = session.get_adapter("https://lll.ansan.go.kr/")
        assert isinstance(adapter, ansan._AnsanLegacyTLSAdapter)
        context = adapter.context()
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert context.options & getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    finally:
        session.close()


def test_lifelong_parser_prioritizes_real_status_and_keeps_cancelled_archive_identity() -> None:
    reg = next(item for item in ansan.ANSAN_LIFELONG_CATALOGUES if item.code == "reg")
    rows, errors = ansan._lll_rows(
        _soup(f"<div class='list-board'>{_lll_card(reg, identity='EDUMNG_12345678', status='교육종료', auxiliary_badge='나이제한', application=False)}</div>"),
        reg,
        1,
    )
    assert not errors and rows[0]["raw_fields"]["source_status"] == "교육종료"

    road = next(item for item in ansan.ANSAN_LIFELONG_CATALOGUES if item.code == "road")
    cancelled, errors = ansan._lll_rows(
        _soup(f"<div class='list-board'>{_lll_card(road, identity='', title='폐강된 규방공예', status='폐강', start='2099-08-31', end='2099-07-01', venue='단원 길거리학습관', linked=False, application=False)}</div>"),
        road,
        3,
    )
    assert not errors
    row = cancelled[0]
    assert row["raw_fields"]["terminal_excluded"] is True
    assert row["raw_fields"]["source_method"] == "cancelled_list_identity"
    assert row["raw_fields"]["source_identity"].startswith("CANCELLED_")
    assert (row["start_date"], row["end_date"]) == ("2099-07-01", "2099-08-31")


def test_reserve_native_waitlist_no_control_override_and_spoof_rejection() -> None:
    category = ansan.ANSAN_RESERVE_CATEGORIES[-1]
    rows, errors = ansan._reserve_rows(
        _soup(_reserve_page(cards=_reserve_card(), total=1)), category, 1
    )
    assert not errors

    waitlist = rows[0]
    errors = ansan._enrich_reserve_detail(waitlist, _soup(_reserve_detail()))
    assert not errors
    assert waitlist["status"] == "OPEN"
    assert waitlist["application_url"] == waitlist["raw_url"]
    assert waitlist["reservation_available"] is True
    assert waitlist["raw_fields"]["status_control_override"] == "waitlist_control_available"
    assert waitlist["municipality_code"] == ansan.ANSAN_DANWON_CODE

    rows, _ = ansan._reserve_rows(
        _soup(_reserve_page(cards=_reserve_card(identity="RESR_000000000019998"), total=1)), category, 1
    )
    unavailable = rows[0]
    assert not ansan._enrich_reserve_detail(
        unavailable,
        _soup(_reserve_detail(identity="RESR_000000000019998", control="")),
    )
    assert unavailable["status"] == "CLOSED"
    assert unavailable["application_url"] == ""
    assert unavailable["raw_fields"]["status_control_override"] == "source_open_without_public_application_control"

    rows, _ = ansan._reserve_rows(
        _soup(_reserve_page(cards=_reserve_card(identity="RESR_000000000019997"), total=1)), category, 1
    )
    spoof_errors = ansan._enrich_reserve_detail(
        rows[0],
        _soup(_reserve_detail(identity="RESR_000000000019997", control="예약신청", control_onclick="location.href='https://evil.test'")),
    )
    assert any("spoofed reservation application control" in error for error in spoof_errors)


def test_legacy_reserve_uses_link_flag_usage_period_and_audited_external_control() -> None:
    category = ansan.ANSAN_RESERVE_CATEGORIES[0]
    cards = _reserve_card(
        identity="25784269",
        link_yn="Y",
        title="외부 공식 영어교실",
        branch="평생학습관",
    )
    rows, errors = ansan._reserve_rows(_soup(_reserve_page(cards=cards, total=1)), category, 1)
    assert not errors
    row = rows[0]
    assert parse_qs(urlparse(row["raw_url"]).query)["linkYn"] == ["Y"]
    errors = ansan._enrich_reserve_detail(
        row,
        _soup(
            _reserve_detail(
                identity="25784269",
                link_yn="Y",
                title="외부 공식 영어교실",
                branch="평생학습관",
                period_label="이용기간",
                control="예약신청",
            )
        ),
    )
    assert not errors
    assert row["reservation_available"] is True
    assert row["application_type"] == "ONLINE_EXTERNAL_LINK"
    assert row["application_url"] == row["raw_url"]


def test_legacy_reserve_decodes_one_extra_title_entity_layer() -> None:
    category = ansan.ANSAN_RESERVE_CATEGORIES[-1]
    rows, errors = ansan._reserve_rows(
        _soup(
            _reserve_page(
                cards=_reserve_card(
                    identity="25855534",
                    link_yn="Y",
                    title="한식조리기능사&amp;amp;김장김치",
                    status="접수마감",
                    branch="여성비전센터",
                ),
                total=1,
            )
        ),
        category,
        1,
    )
    assert not errors
    row = rows[0]
    assert row["title"] == "한식조리기능사&김장김치"
    assert not ansan._enrich_reserve_detail(
        row,
        _soup(
            _reserve_detail(
                identity="25855534",
                link_yn="Y",
                title="한식조리기능사&amp;김장김치",
                status="접수마감",
                branch="여성비전센터",
                control="",
            )
        ),
    )


def test_closed_list_accepts_open_detail_only_when_full_and_no_control() -> None:
    category = next(
        item for item in ansan.ANSAN_RESERVE_CATEGORIES if item.code == "E05"
    )
    cards = _reserve_card(
        identity="RESR_000000000019415",
        link_yn="N",
        title="다이어트댄스(야간)",
        status="접수마감",
        branch="고잔동",
    )
    rows, errors = ansan._reserve_rows(
        _soup(_reserve_page(cards=cards, total=1)), category, 1
    )
    assert not errors
    row = rows[0]
    assert not ansan._enrich_reserve_detail(
        row,
        _soup(
            _reserve_detail(
                identity="RESR_000000000019415",
                title="다이어트댄스(야간)",
                status="접수중",
                branch="고잔동",
                capacity="35명/35명",
                control="",
            )
        ),
    )
    assert row["status"] == "CLOSED"
    assert row["reservation_available"] is False
    assert row["application_url"] == ""
    assert row["raw_fields"]["status_control_override"] == (
        "list_closed_detail_open_full_without_application_control"
    )

    rows, _ = ansan._reserve_rows(
        _soup(_reserve_page(cards=cards, total=1)), category, 1
    )
    mismatch = ansan._enrich_reserve_detail(
        rows[0],
        _soup(
            _reserve_detail(
                identity="RESR_000000000019415",
                title="다이어트댄스(야간)",
                status="접수중",
                branch="고잔동",
                capacity="34명/35명",
                control="",
            )
        ),
    )
    assert any("reservation detail status mismatch" in error for error in mismatch)


def test_scheduled_numeric_legacy_accepts_only_exact_unpublished_official_shell() -> None:
    category = ansan.ANSAN_RESERVE_CATEGORIES[-1]
    rows, errors = ansan._reserve_rows(
        _soup(
            _reserve_page(
                cards=_reserve_card(
                    identity="25787042",
                    link_yn="Y",
                    status="접수대기",
                    branch="스마트복합문화센터",
                ),
                total=1,
            )
        ),
        category,
        1,
    )
    assert not errors
    row = rows[0]
    assert not ansan._enrich_reserve_detail(
        row, _soup(_reserve_unpublished_shell())
    )
    assert row["status"] == "SCHEDULED"
    assert row["application_type"] == "INFORMATION_ONLY"
    assert row["reservation_available"] is False
    assert row["application_url"] == ""
    assert row["raw_fields"]["status_control_override"] == (
        "scheduled_legacy_detail_not_yet_published"
    )


def test_complete_open_legacy_list_row_keeps_information_but_disables_application() -> None:
    category = ansan.ANSAN_RESERVE_CATEGORIES[-1]
    rows, errors = ansan._reserve_rows(
        _soup(
            _reserve_page(
                cards=_reserve_card(
                    identity="25787042",
                    link_yn="Y",
                    status="접수중",
                ),
                total=1,
            )
        ),
        category,
        1,
    )
    assert not errors
    detail_errors = ansan._enrich_reserve_detail(
        rows[0], _soup(_reserve_unpublished_shell())
    )
    assert not detail_errors
    assert rows[0]["status"] == "CLOSED"
    assert rows[0]["application_url"] == ""
    assert rows[0]["reservation_available"] is False
    assert rows[0]["raw_fields"]["status_control_override"] == (
        "source_open_legacy_detail_unpublished_without_public_control"
    )


def test_unpublished_shell_rejects_waitlist_or_incomplete_open_list_evidence() -> None:
    category = ansan.ANSAN_RESERVE_CATEGORIES[-1]
    rows, errors = ansan._reserve_rows(
        _soup(
            _reserve_page(
                cards=_reserve_card(
                    identity="25787042",
                    link_yn="Y",
                    status="대기자접수",
                ),
                total=1,
            )
        ),
        category,
        1,
    )
    assert not errors
    assert ansan._enrich_reserve_detail(
        rows[0], _soup(_reserve_unpublished_shell())
    )

    rows, errors = ansan._reserve_rows(
        _soup(
            _reserve_page(
                cards=_reserve_card(
                    identity="25787043",
                    link_yn="Y",
                    status="접수중",
                ),
                total=1,
            )
        ),
        category,
        1,
    )
    assert not errors
    rows[0]["target"] = ""
    assert ansan._enrich_reserve_detail(
        rows[0], _soup(_reserve_unpublished_shell())
    )


def test_unpublished_shell_rejects_message_and_identity_shape_mutations() -> None:
    category = ansan.ANSAN_RESERVE_CATEGORIES[-1]
    rows, _ = ansan._reserve_rows(
        _soup(
            _reserve_page(
                cards=_reserve_card(
                    identity="25787042", link_yn="Y", status="접수대기"
                ),
                total=1,
            )
        ),
        category,
        1,
    )
    assert ansan._enrich_reserve_detail(
        rows[0], _soup(_reserve_unpublished_shell("교육정보를 준비 중입니다."))
    )

    rows, _ = ansan._reserve_rows(
        _soup(
            _reserve_page(
                cards=_reserve_card(
                    identity="RESR_000000000019996",
                    link_yn="N",
                    status="접수대기",
                ),
                total=1,
            )
        ),
        category,
        1,
    )
    assert ansan._enrich_reserve_detail(
        rows[0], _soup(_reserve_unpublished_shell())
    )


def test_cross_source_reconciliation_requires_strong_unambiguous_match() -> None:
    lifelong = {
        "provider_course_id": "lifelong-id",
        "title": "브라보 마이 라이프",
        "start_date": "2099-07-01",
        "end_date": "2099-08-31",
        "schedule_raw": "월요일 10:00 ~ 12:00",
        "branch": ansan.ANSAN_MAIN_CENTER,
        "raw_fields": {"source_kind": "lifelong", "source_identity": "NOREDU_12345678"},
    }
    reserve = {
        "provider_course_id": "reserve-id",
        "title": "브라보 마이 라이프",
        "start_date": "2099-07-01",
        "end_date": "2099-08-31",
        "schedule_raw": "월요일 10:00 ~ 12:00 교육실",
        "branch": "평생학습관",
        "raw_fields": {"source_kind": "reserve", "source_identity": "RESR_000000000019999"},
    }
    reconciled, errors, overlaps = ansan._reconcile_cross_source_overlaps([lifelong, reserve])
    assert not errors and reconciled == [lifelong] and len(overlaps) == 1
    assert lifelong["raw_fields"]["semantic_overlap_owner"]["replica_identity"] == "RESR_000000000019999"

    other = dict(reserve, provider_course_id="reserve-other", branch="단원청소년수련관")
    other["raw_fields"] = dict(reserve["raw_fields"], source_identity="RESR_000000000019998")
    _rows, errors, overlaps = ansan._reconcile_cross_source_overlaps([dict(lifelong), other])
    assert not overlaps
    assert any("not a learning-center replica" in error for error in errors)


def test_reg_category_prefix_and_open_missing_detail_require_strong_replica_proof() -> None:
    lifelong = {
        "provider_course_id": "lifelong-reg-id",
        "title": "[인문교양] 시민 글쓰기",
        "start_date": "2099-07-01",
        "end_date": "2099-08-31",
        "schedule_raw": "화요일 14:00 ~ 16:00",
        "branch": ansan.ANSAN_MAIN_CENTER,
        "raw_fields": {
            "source_kind": "lifelong",
            "source_catalogue": "reg",
            "source_identity": "EDUMNG_12345678",
        },
    }
    category = ansan.ANSAN_RESERVE_CATEGORIES[-1]
    parsed, errors = ansan._reserve_rows(
        _soup(
            _reserve_page(
                cards=_reserve_card(
                    identity="25854099",
                    link_yn="Y",
                    title="시민 글쓰기",
                    branch="평생학습관",
                ),
                total=1,
            )
        ),
        category,
        1,
    )
    assert not errors
    replica = parsed[0]

    list_only = deepcopy(replica)
    assert not ansan._enrich_reserve_detail(
        list_only, _soup(_reserve_unpublished_shell())
    )
    assert list_only["status"] == "CLOSED"
    assert list_only["raw_fields"]["status_control_override"] == (
        "source_open_legacy_detail_unpublished_without_public_control"
    )

    assert not ansan._enrich_reserve_detail(
        replica,
        _soup(_reserve_unpublished_shell()),
        allow_open_legacy_semantic_replica=True,
    )
    assert replica["raw_fields"]["status_control_override"] == (
        "open_legacy_semantic_replica_missing_detail"
    )

    reconciled, overlap_errors, overlaps = ansan._reconcile_cross_source_overlaps(
        [lifelong, replica]
    )
    assert not overlap_errors
    assert reconciled == [lifelong]
    assert overlaps == [
        {
            "title": "[인문교양] 시민 글쓰기",
            "lifelong_identity": "EDUMNG_12345678",
            "reserve_identity": "25854099",
        }
    ]

    unknown_prefix = dict(lifelong, provider_course_id="unknown-prefix")
    unknown_prefix["title"] = "[임의분류] 시민 글쓰기"
    unknown_prefix["raw_fields"] = dict(lifelong["raw_fields"])
    _rows, overlap_errors, overlaps = ansan._reconcile_cross_source_overlaps(
        [unknown_prefix, replica]
    )
    assert not overlap_errors and not overlaps


def test_complete_synthetic_snapshot_retries_invalid_details_and_keeps_pii_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalogue = ansan.ANSAN_LIFELONG_CATALOGUES[0]
    category = ansan.ANSAN_RESERVE_CATEGORIES[-1]
    monkeypatch.setattr(ansan, "ANSAN_LIFELONG_CATALOGUES", (catalogue,))
    monkeypatch.setattr(ansan, "ANSAN_RESERVE_CATEGORIES", (category,))
    monkeypatch.setattr(ansan, "_LLL_BY_CODE", {catalogue.code: catalogue})
    monkeypatch.setattr(ansan, "_RESERVE_BY_CODE", {category.code: category})
    monkeypatch.setattr(ansan, "ANSAN_LLL_PAGE_SIZE", 1)
    monkeypatch.setattr(ansan, "ANSAN_RESERVE_PAGE_SIZE", 1)

    lll_list = _lll_page(catalogue, cards=_lll_card(catalogue), total=1)
    lll_empty = _lll_page(catalogue, cards="", total=1)
    reserve_list = _reserve_page(
        cards=_reserve_card(
            identity="25787042",
            link_yn="Y",
            status="접수대기",
            branch="단원구청",
        ),
        total=1,
    )
    reserve_empty = _reserve_page(cards="", total=1)
    road_list = _road_page(card=True)
    road_empty = _road_page(card=False)
    good_details = {
        ansan.ansan_lifelong_detail_url(catalogue, "NOREDU_12345678"): _lll_detail(),
        ansan.ansan_reserve_detail_url(category, "25787042", "Y"): (
            _reserve_unpublished_shell()
        ),
    }
    pages = {
        ansan.ansan_lifelong_list_url(catalogue, 1): lll_list,
        ansan.ansan_lifelong_list_url(catalogue, 2): lll_empty,
        ansan.ansan_reserve_list_url(category, 1): reserve_list,
        ansan.ansan_reserve_list_url(category, 2): reserve_empty,
        ansan.ansan_road_place_list_url(1): road_list,
        ansan.ansan_road_place_list_url(2): road_empty,
    }
    counts: Counter[str] = Counter()
    lock = Lock()

    def fetch(_session: Any, url: str, _timeout: int) -> str:
        with lock:
            counts[url] += 1
            count = counts[url]
        if url in good_details:
            return "<html><body>일시적으로 비어 있는 상세</body></html>" if count == 1 else good_details[url]
        if url not in pages:
            raise AssertionError(f"unexpected URL {url}")
        return pages[url]

    sessions: list[FakeSession] = []

    def factory() -> FakeSession:
        current = FakeSession()
        sessions.append(current)
        return current

    rows, parser, meta = ansan.collect_ansan_education_courses(
        {"provider": ansan.ANSAN_PROVIDER, "url": ansan.ANSAN_CANONICAL_URL},
        fetcher=fetch,
        session_factory=factory,
        dedupe_rows=lambda values: values,
        today="2099-07-21",
        max_pages=20,
        detail_limit=10,
        max_workers=4,
    )

    assert parser == ansan.ANSAN_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["partitions_complete"] is True
    assert meta["details_complete"] is True
    assert meta["source_total"] == 2
    assert meta["road_place_total"] == 1
    assert meta["current_count"] == 2
    assert meta["detail_pages"] == 2
    assert meta["detail_retry_pages"] == 2
    assert meta["detail_errors"] == 0
    assert meta["scheduled_detail_unpublished_count"] == 1
    assert meta["worker_limit"] == 16
    assert meta["detail_retry_worker_limit"] == 6
    assert meta["detail_batch_size"] == 64
    assert meta["returned_count"] == len(rows) == 2
    assert {row["municipality_code"] for row in rows} == {
        ansan.ANSAN_SANGNOK_CODE,
        ansan.ANSAN_DANWON_CODE,
    }
    assert all(set(row["raw_fields"]) <= ansan.ANSAN_RAW_FIELD_ALLOWLIST for row in rows)
    assert "문의" not in repr(rows) and "강사" not in repr(rows) and "010-" not in repr(rows)
    assert sessions and all(session.closed for session in sessions)


def test_complete_snapshot_drops_prevalidated_open_reg_replica_with_exact_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalogue = next(
        item for item in ansan.ANSAN_LIFELONG_CATALOGUES if item.code == "reg"
    )
    category = next(
        item for item in ansan.ANSAN_RESERVE_CATEGORIES if item.code == "E06"
    )
    monkeypatch.setattr(ansan, "ANSAN_LIFELONG_CATALOGUES", (catalogue,))
    monkeypatch.setattr(ansan, "ANSAN_RESERVE_CATEGORIES", (category,))
    monkeypatch.setattr(ansan, "_LLL_BY_CODE", {catalogue.code: catalogue})
    monkeypatch.setattr(ansan, "_RESERVE_BY_CODE", {category.code: category})
    monkeypatch.setattr(ansan, "ANSAN_LLL_PAGE_SIZE", 1)
    monkeypatch.setattr(ansan, "ANSAN_RESERVE_PAGE_SIZE", 1)

    lifelong_identity = "EDUMNG_12345678"
    reserve_identity = "25854099"
    lifelong_title = "[인문교양] 시민 글쓰기"
    reserve_title = "시민 글쓰기"
    lifelong_list = _lll_page(
        catalogue,
        cards=_lll_card(
            catalogue,
            identity=lifelong_identity,
            title=lifelong_title,
            day="화요일",
            time="14:00 ~ 16:00",
        ),
        total=1,
    )
    reserve_list = _reserve_page(
        cards=_reserve_card(
            identity=reserve_identity,
            link_yn="Y",
            title=reserve_title,
            branch="평생학습관",
        ),
        total=1,
    )
    pages = {
        ansan.ansan_lifelong_list_url(catalogue, 1): lifelong_list,
        ansan.ansan_lifelong_list_url(catalogue, 2): _lll_page(
            catalogue, cards="", total=1
        ),
        ansan.ansan_reserve_list_url(category, 1): reserve_list,
        ansan.ansan_reserve_list_url(category, 2): _reserve_page(cards="", total=1),
        ansan.ansan_road_place_list_url(1): _road_page(card=True),
        ansan.ansan_road_place_list_url(2): _road_page(card=False),
        ansan.ansan_lifelong_detail_url(catalogue, lifelong_identity): _lll_detail(
            identity=lifelong_identity,
            title=lifelong_title,
        ),
        ansan.ansan_reserve_detail_url(category, reserve_identity, "Y"): (
            _reserve_unpublished_shell()
        ),
    }

    def fetch(_session: Any, url: str, _timeout: int) -> str:
        if url not in pages:
            raise AssertionError(f"unexpected URL {url}")
        return pages[url]

    rows, parser, meta = ansan.collect_ansan_education_courses(
        {"provider": ansan.ANSAN_PROVIDER, "url": ansan.ANSAN_CANONICAL_URL},
        fetcher=fetch,
        session_factory=FakeSession,
        dedupe_rows=lambda values: values,
        today="2099-07-21",
        max_pages=9,
        detail_limit=2,
        max_workers=2,
    )

    assert parser == ansan.ANSAN_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == meta["current_count"] == 2
    assert meta["detail_attempts"] == meta["detail_pages"] == 2
    assert meta["open_legacy_replica_shell_count"] == 1
    assert meta["cross_source_overlap_count"] == 1
    assert meta["configured_collection_error"] == ""
    assert len(rows) == 1
    assert rows[0]["raw_fields"]["source_identity"] == lifelong_identity
    assert rows[0]["raw_fields"]["semantic_overlap_owner"] == {
        "preferred_source": "lifelong",
        "replica_source": "reserve",
        "replica_identity": reserve_identity,
    }


def test_detail_validation_batches_are_bounded_and_copy_rows() -> None:
    source_rows = {
        str(index): {
            "raw_url": f"https://example.test/{index}",
            "title": f"원본 {index}",
        }
        for index in range(130)
    }
    sessions: list[FakeSession] = []

    def factory() -> FakeSession:
        current = FakeSession()
        sessions.append(current)
        return current

    def fetch(_session: Any, _url: str, _timeout: int) -> str:
        return "<html><body>정상 상세</body></html>"

    def validate(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
        assert soup.get_text(strip=True) == "정상 상세"
        row["title"] = "검증 완료"
        return []

    valid, failures, response_pages = ansan._parallel_detail_validations(
        source_rows,
        fetcher=fetch,
        session_factory=factory,
        timeout=1,
        max_workers=4,
        batch_size=10_000,
        validator=validate,
    )

    assert not failures and response_pages == len(valid) == 130
    assert all(row["title"] == "검증 완료" for row in valid.values())
    assert all(row["title"].startswith("원본 ") for row in source_rows.values())
    # 130 rows are split into 64 + 64 + 2; each batch owns at most 4 sessions.
    assert 3 <= len(sessions) <= 12
    assert all(session.closed for session in sessions)


def test_missing_managed_session_and_wrong_target_fail_without_fetching() -> None:
    target = {"provider": ansan.ANSAN_PROVIDER, "url": ansan.ANSAN_CANONICAL_URL}
    rows, _parser, meta = ansan.collect_ansan_education_courses(target)
    assert rows == [] and "session_factory injection" in meta["configured_collection_error"]

    called = False

    def fetch(_session: Any, _url: str, _timeout: int) -> str:
        nonlocal called
        called = True
        raise AssertionError

    rows, _parser, meta = ansan.collect_ansan_education_courses(
        {**target, "provider": "WRONG"}, fetcher=fetch
    )
    assert rows == [] and called is False
    assert "canonical Ansan education owner" in meta["configured_collection_error"]
