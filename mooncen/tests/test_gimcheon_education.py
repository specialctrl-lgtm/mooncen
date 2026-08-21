from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import math
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_gimcheon as gimcheon


@dataclass(frozen=True)
class _Record:
    identity: str
    rcpt_no: str
    title: str
    subcategory: str = "문화예술향상"
    method: str = "온라인"
    venue: str = "평생교육원(본관) 2강의실"
    period: str = "2026-08-03 ~ 2026-12-18"
    schedule_time: str = "10:00 ~ 12:00"
    schedule_days: str = "목"
    apply_period: str = "2026-07-23 09:00 ~ 2026-07-30 18:00"
    capacity: str = "0 / 15명"
    waiting_capacity: str = "0 / 5명"
    fee: str = "40,000원"
    source_state: str = "W"


RECORDS = tuple(
    _Record(str(106 - index), str(506 - index), f"정기강좌 {index + 1}")
    for index in range(6)
)


def _target(
    *,
    provider: str = gimcheon.GIMCHEON_PROVIDER,
    url: str = gimcheon.GIMCHEON_CANONICAL_URL,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "url": url,
        "name": "김천시평생교육원 교육프로그램",
        "branch": gimcheon.GIMCHEON_MUNICIPALITY_NAME,
        "extra": {},
    }


def _menu(*, drift: bool = False) -> str:
    links = "".join(
        (
            '<li class="depth2"><a '
            f'href="{section.menu_href}">{section.label}</a></li>'
        )
        for section in gimcheon.GIMCHEON_SECTIONS
    )
    if drift:
        links += (
            '<li class="depth2"><a '
            'href="/welfare/page/link.tc?mn=10999&pageNo=10999">새 교육</a></li>'
        )
    return (
        '<ul id="gnb"><li class="depth1 current"><a href="#">교육프로그램</a>'
        f"<ul>{links}</ul></li></ul>"
    )


def _pager(*, requested: int, last: int) -> str:
    links = []
    for page in range(1, last + 1):
        active = ' class="active"' if page == requested else ""
        links.append(
            f'<li><a{active} href="javascript:;" '
            f'onclick="eduList.pageMove({page});">{page}</a></li>'
        )
    return f'<div class="pager_a"><ol class="pager">{"".join(links)}</ol></div>'


def _state_label(state: str) -> str:
    return {"W": "대기", "I": "접수", "E": "마감"}[state]


def _registration(record: _Record, *, detail: bool) -> str:
    prefix = "eduDetail" if detail else "eduList"
    return f"""
      <ul class="accept"><li>
        <a href="javascript:;" onclick="{prefix}.apply('{record.rcpt_no}', '{record.source_state}')">
          <p>일반접수</p>
          <ol>
            <li><em>기 간</em><span>{record.apply_period}</span></li>
            <li><em>정 원</em><span>{record.capacity}</span></li>
            <li><em>후 보</em><span>{record.waiting_capacity}</span></li>
            <li><em>교 육 비</em><span>{record.fee}</span></li>
          </ol>
          <span>{_state_label(record.source_state)}</span>
        </a>
      </li></ul>
    """


def _course_fields(record: _Record, *, detail: bool) -> str:
    values = {
        "상세분류": record.subcategory,
        "접수방법": record.method,
        "강의실": record.venue,
        "교육기간": record.period,
        "교육시간": record.schedule_time,
        "교육요일": record.schedule_days,
    }
    order = (
        ("상세분류", "접수방법", "교육기간", "강의실", "교육시간", "교육요일")
        if detail
        else ("상세분류", "접수방법", "강의실", "교육기간", "교육시간", "교육요일")
    )
    return "".join(
        (
            '<li class="list_item">'
            f'<span class="tit">{label}</span><p class="txt">{values[label]}</p>'
            "</li>"
        )
        for label in order
    )


def _card(record: _Record, *, detail: bool = False) -> str:
    detail_control = ""
    if not detail:
        detail_control = (
            '<a class="Tbtn" href="javascript:;" '
            f'onclick="eduList.detail(\'{record.identity}\')">상세보기</a>'
        )
    return f"""
      <div class="class_item list_s">
        <div class="desc">
          <div class="top"><div class="left">
            <div class="tag">문화예술교육 <span class="round">{_state_label(record.source_state)}</span></div>
            <span class="tit">{record.title}</span>
          </div><div class="right">{detail_control}</div></div>
          <ul class="list">{_course_fields(record, detail=detail)}</ul>
        </div>
        {_registration(record, detail=detail)}
      </div>
    """


def _list_html(
    section: gimcheon.GimcheonSection,
    records: tuple[_Record, ...],
    *,
    requested: int,
    last: int,
    menu_drift: bool = False,
) -> str:
    if records:
        ledger = "".join(_card(record) for record in records)
    else:
        ledger = (
            '<div style="display:block"><div class="desc text-center">'
            "모집중인 강좌가 없습니다.</div></div>"
        )
    page_index = "" if requested == 1 else str(requested)
    return f"""
      <html><head><title>교육프로그램 &gt; {section.label} | 김천시평생교육원</title></head>
      <body>{_menu(drift=menu_drift)}
        <form id="eduListForm" method="get">
          <input name="pageDtlOrdrNo" value="{section.detail_order}">
          <input name="pageIndex" value="{page_index}">
          <input name="operNo" value="0"><input name="rcptNo" value="0">
          <input name="pageNo" value="{section.page_no}">
          <input name="searchTrgtSeCd" value="{section.target_code}">
          <div class="calss_wrap">{ledger}</div>
          {_pager(requested=requested, last=last)}
        </form>
      </body></html>
    """


def _detail_html(
    section: gimcheon.GimcheonSection,
    record: _Record,
    *,
    title_override: str = "",
    registration_override: _Record | None = None,
    direct_application_endpoint: bool = False,
) -> str:
    shown = replace(record, title=title_override) if title_override else record
    registration = registration_override or shown
    endpoint = (
        '<a href="/welfare/edu/apply.tc?rcptNo=999">신청하기</a>'
        if direct_application_endpoint
        else ""
    )
    return f"""
      <html><head><title>교육프로그램 &gt; {section.label} | 김천시평생교육원</title></head>
      <body><form id="eduDetailForm" method="get">
        <input name="pageDtlOrdrNo" value="{section.detail_order}">
        <input name="pageIndex" value="">
        <input name="searchTrgtSeCd" value="{section.target_code}">
        <input name="operNo" value="{record.identity}">
        <input name="rcptNo" value="0">
        <div class="calss_wrap">{_card(shown, detail=True)}</div>
        <div class="table_shape">
          <div>강사 홍길동</div>
          <div>강의내용 상세 본문 010-1234-5678 teacher@example.org</div>
          <a href="/welfare/file/readFile.tc?fileId=FILE1">강의계획서.pdf</a>
        </div>
        {_registration(registration, detail=True) if False else ""}
        {endpoint}
      </form></body></html>
    """


@dataclass
class _Response:
    text: str
    url: str
    status_code: int = 200


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Fetcher:
    def __init__(
        self,
        *,
        records: tuple[_Record, ...] = RECORDS,
        menu_drift: bool = False,
        nonempty_sentinel: bool = False,
        detail_title_mismatch: bool = False,
        detail_registration_mismatch: bool = False,
        direct_application_endpoint: bool = False,
    ) -> None:
        self.records = records
        self.menu_drift = menu_drift
        self.nonempty_sentinel = nonempty_sentinel
        self.detail_title_mismatch = detail_title_mismatch
        self.detail_registration_mismatch = detail_registration_mismatch
        self.direct_application_endpoint = direct_application_endpoint
        self.calls: list[str] = []
        self.detail_calls = 0

    def __call__(self, session: Any, url: str, timeout: int) -> _Response:
        del session, timeout
        self.calls.append(url)
        lowered = url.lower()
        assert not any(
            token in lowered
            for token in ("/edu/apply", "/edu/rcpt", "applicant", "applylist", "rcptlist")
        )
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        section = next(
            value
            for value in gimcheon.GIMCHEON_SECTIONS
            if urlparse(value.url).path == parsed.path
        )
        if query.get("importUrl") == ["/edu/detail.tc"]:
            self.detail_calls += 1
            identity = query["operNo"][0]
            record = next(value for value in self.records if value.identity == identity)
            title_override = "다른 상세 제목" if self.detail_title_mismatch else ""
            registration_override = None
            if self.detail_registration_mismatch:
                registration_override = replace(record, rcpt_no="999")
            # _card owns the one detail registration ledger.  Apply a mismatch
            # by replacing the record passed to it while preserving operNo.
            shown = registration_override or record
            html = _detail_html(
                section,
                shown,
                title_override=title_override,
                direct_application_endpoint=self.direct_application_endpoint,
            )
            if registration_override is not None:
                html = html.replace(
                    f'<input name="operNo" value="{registration_override.identity}">',
                    f'<input name="operNo" value="{record.identity}">',
                    1,
                )
            return _Response(html, url)

        page = int((query.get("pageIndex") or ["1"])[0] or "1")
        section_records = self.records if section.key == "regular" else ()
        last = max(1, math.ceil(len(section_records) / gimcheon.GIMCHEON_PAGE_SIZE))
        start = (page - 1) * gimcheon.GIMCHEON_PAGE_SIZE
        selected = tuple(section_records[start : start + gimcheon.GIMCHEON_PAGE_SIZE])
        if page > last:
            selected = (section_records[0],) if self.nonempty_sentinel and section.key == "regular" else ()
        return _Response(
            _list_html(
                section,
                selected,
                requested=page,
                last=last,
                menu_drift=self.menu_drift,
            ),
            url,
        )


def _collect(fetcher: _Fetcher, **kwargs: Any):
    session = _Session()
    rows, parser, meta = gimcheon.collect_gimcheon_education(
        _target(),
        today=date(2026, 7, 23),
        session_factory=lambda: session,
        fetcher=fetcher,
        **kwargs,
    )
    assert session.closed is True
    return rows, parser, meta


def test_target_guard_and_candidate_metadata() -> None:
    assert (
        gimcheon.GIMCHEON_SECTION_BY_KEY["humanities"].target_code
        == "RMS003011"
    )
    assert gimcheon.is_gimcheon_education_target(_target())
    assert not gimcheon.is_gimcheon_education_target(
        _target(url=gimcheon.GIMCHEON_OLD_URL)
    )
    assert not gimcheon.is_gimcheon_education_target(
        _target(provider="MUNI_WRONG")
    )
    assert not gimcheon.is_gimcheon_education_target(
        _target(url=gimcheon.GIMCHEON_CANONICAL_URL + "?pageIndex=1")
    )
    assert (
        gimcheon.GIMCHEON_EXCLUDED_OWNER_BOUNDARIES[gimcheon.GIMCHEON_OLD_URL]
        == "authenticated_personal_education_application_history_not_public_course_ledger"
    )
    assert gimcheon.GIMCHEON_CANONICAL_CANDIDATE_ID == "MUNI_IR_25705DD6ADAA"
    assert gimcheon.GIMCHEON_OLD_CANDIDATE_ID == "MUNI_IR_A70EA9B39330"


def test_complete_five_section_snapshot_is_detail_bound_and_pii_safe() -> None:
    fetcher = _Fetcher()
    rows, parser, meta = _collect(fetcher, max_pages=10, detail_limit=20)

    assert parser == gimcheon.GIMCHEON_PARSER
    assert len(rows) == 6
    assert meta["full_snapshot_validated"] is True
    assert meta["section_pages"] == {
        "평생교육 정기강좌": 2,
        "수시강좌": 1,
        "사회적배려계층": 1,
        "김천시여성대학": 1,
        "핵심 인문학 특강": 1,
    }
    assert meta["section_counts"] == {
        "평생교육 정기강좌": 6,
        "수시강좌": 0,
        "사회적배려계층": 0,
        "김천시여성대학": 0,
        "핵심 인문학 특강": 0,
    }
    assert meta["boundary_modes"] == {
        section.key: "exact_structural_empty"
        for section in gimcheon.GIMCHEON_SECTIONS
    }
    assert meta["sentinel_pages"] == {
        "regular": 3,
        "rolling": 2,
        "social": 2,
        "women": 2,
        "humanities": 2,
    }
    assert meta["source_total"] == meta["current_source_count"] == 6
    assert meta["source_status_counts"] == {"W": 6}
    assert meta["current_status_counts"] == {"SCHEDULED": 6}
    assert meta["detail_verified"] == 6
    assert meta["application_control_count"] == 0
    assert meta["list_requests"] == 22
    assert meta["logical_requests"] == meta["physical_requests"] == 28
    assert fetcher.detail_calls == 6
    assert all(row["branch"] == gimcheon.GIMCHEON_BRANCH for row in rows)
    assert all(row["address"] == gimcheon.GIMCHEON_ADDRESS for row in rows)
    assert all(row["status"] == "SCHEDULED" for row in rows)
    assert all(row["application_url"] == "" for row in rows)
    assert all(row["reservation_available"] is False for row in rows)
    assert all(row["raw_fields"]["detail_verified"] is True for row in rows)
    serialized = repr(rows)
    assert "010-1234-5678" not in serialized
    assert "teacher@example.org" not in serialized
    assert "강의내용 상세 본문" not in serialized
    assert "강의계획서.pdf" not in serialized


def test_ended_registration_remains_a_current_closed_course() -> None:
    closed = replace(RECORDS[0], source_state="E")
    rows, _, meta = _collect(
        _Fetcher(records=(closed,)), max_pages=10, detail_limit=5
    )

    assert meta["full_snapshot_validated"] is True
    assert meta["source_status_counts"] == {"E": 1}
    assert meta["current_status_counts"] == {"CLOSED": 1}
    assert len(rows) == 1
    assert rows[0]["status"] == "CLOSED"
    assert rows[0]["application_url"] == ""
    assert rows[0]["reservation_available"] is False


def test_exact_owner_menu_drift_fails_closed() -> None:
    rows, _, meta = _collect(_Fetcher(menu_drift=True))
    assert rows == []
    assert "owner menu vocabulary changed" in meta["configured_collection_error"]
    assert meta["full_snapshot_validated"] is False


def test_exact_post_last_sentinel_must_be_empty() -> None:
    rows, _, meta = _collect(_Fetcher(nonempty_sentinel=True))
    assert rows == []
    assert "post-last page" in meta["configured_collection_error"]
    assert meta["detail_pages"] == 0


def test_page_and_detail_caps_fail_before_partial_publication() -> None:
    page_rows, _, page_meta = _collect(_Fetcher(), max_pages=1)
    assert page_rows == []
    assert page_meta["source_cap_reached"] is True
    assert "exceeds max_pages=1" in page_meta["configured_collection_error"]

    detail_fetcher = _Fetcher()
    detail_rows, _, detail_meta = _collect(detail_fetcher, detail_limit=5)
    assert detail_rows == []
    assert detail_meta["source_cap_reached"] is True
    assert "partial current/future snapshot" in detail_meta["configured_collection_error"]
    assert detail_fetcher.detail_calls == 0


def test_detail_title_and_registration_bindings_fail_closed() -> None:
    rows, _, meta = _collect(_Fetcher(detail_title_mismatch=True))
    assert rows == []
    assert "list/detail title mismatch" in meta["configured_collection_error"]

    rows, _, meta = _collect(_Fetcher(detail_registration_mismatch=True))
    assert rows == []
    assert "list/detail registration binding changed" in meta["configured_collection_error"]


def test_direct_application_endpoint_is_rejected_and_never_requested() -> None:
    fetcher = _Fetcher(direct_application_endpoint=True)
    rows, _, meta = _collect(fetcher)
    assert rows == []
    assert "unexpected direct application endpoint" in meta["configured_collection_error"]
    assert meta["application_endpoint_requests"] == 0
    assert meta["applicant_list_requests"] == 0
    assert all("/edu/apply.tc" not in url for url in fetcher.calls)


def test_enabled_source_state_exposes_only_the_safe_detail_entry_url() -> None:
    records = tuple(replace(record, source_state="I") for record in RECORDS)
    fetcher = _Fetcher(records=records)
    rows, _, meta = _collect(fetcher)
    assert len(rows) == 6
    assert meta["source_status_counts"] == {"I": 6}
    assert meta["current_status_counts"] == {"OPEN": 6}
    assert meta["application_control_count"] == 6
    assert all(row["status"] == "OPEN" for row in rows)
    assert all(row["reservation_available"] is True for row in rows)
    assert all(row["application_url"] == row["raw_url"] for row in rows)
    assert all("/edu/apply" not in url.lower() for url in fetcher.calls)


def test_contact_data_in_a_public_output_field_fails_closed() -> None:
    records = (replace(RECORDS[0], title="문의 010-9999-8888"), *RECORDS[1:])
    rows, _, meta = _collect(_Fetcher(records=records))
    assert rows == []
    assert "public row leaked contact data" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_GIMCHEON_TESTS") != "1",
    reason="set RUN_LIVE_GIMCHEON_TESTS=1 for the audited live snapshot",
)
def test_live_snapshot_2026_07_23() -> None:
    rows, parser, meta = gimcheon.collect_gimcheon_education(
        _target(),
        today=date(2026, 7, 23),
        max_pages=10,
        detail_limit=50,
        timeout=30,
    )
    assert parser == gimcheon.GIMCHEON_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["full_snapshot_validated"] is True
    assert meta["section_pages"] == {
        "평생교육 정기강좌": 7,
        "수시강좌": 1,
        "사회적배려계층": 1,
        "김천시여성대학": 1,
        "핵심 인문학 특강": 1,
    }
    assert meta["section_counts"] == {
        "평생교육 정기강좌": 35,
        "수시강좌": 0,
        "사회적배려계층": 0,
        "김천시여성대학": 0,
        "핵심 인문학 특강": 0,
    }
    assert len(rows) == meta["source_total"] == meta["current_source_count"] == 35
    assert meta["source_status_counts"] == {"W": 35}
    assert meta["current_status_counts"] == {"SCHEDULED": 35}
    assert meta["detail_verified"] == 35
    assert meta["application_control_count"] == 0
    assert meta["application_endpoint_requests"] == 0
    assert meta["applicant_list_requests"] == 0
    assert meta["list_requests"] == 27
    assert meta["logical_requests"] == meta["physical_requests"] == 62
    assert all(row["branch"] == gimcheon.GIMCHEON_BRANCH for row in rows)
    assert all(row["address"] == gimcheon.GIMCHEON_ADDRESS for row in rows)
    assert all(row["reservation_available"] is False for row in rows)
