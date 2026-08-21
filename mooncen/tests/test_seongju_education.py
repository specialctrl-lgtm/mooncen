from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import math
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_seongju as seongju


@dataclass(frozen=True)
class _Record:
    ledger: str
    identity: str
    number: str
    title: str
    day: str = "목"
    time: str = "13:00 ~ 15:00"
    period: str = "`26-09-03 ~ `26-12-24"
    capacity: str = "0/ 16"
    status: str = "접수마감"
    target: str = "18세 이상 성주군민 16명"
    venue: str = "창의문화센터 B동 3층 문화교실3"
    fee: str = "20,000원"
    material_fee: str = ""


HAPPINESS = tuple(
    _Record(
        "happiness",
        str(200 + index),
        str(11 - index),
        f"군민행복 강좌 {index + 1}",
        status="접수중" if index == 0 else "접수대기" if index == 1 else "접수마감",
        venue=(
            "문화예술회관 양재교육장"
            if index % 3 == 0
            else "창의문화센터 A동 2층 문화교실1"
        ),
    )
    for index in range(11)
)
YOUTH = (
    _Record(
        "youth",
        "301",
        "1",
        "성주군청소년문화의집 겨울특별 프로그램",
        day="토",
        time="10:00 ~ 12:00",
        period="`26-12-26 ~ `26-12-26",
        capacity="0/ 12",
        target="성주군 아동·청소년",
        venue="성주군청소년문화의집",
        fee="0원",
        material_fee="0원",
    ),
)
RECORDS = HAPPINESS + YOUTH


def _target(
    *,
    provider: str = seongju.SEONGJU_PROVIDER,
    url: str = seongju.SEONGJU_CANONICAL_URL,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "url": url,
        "name": "성주복지플랫폼 교육 원장",
        "branch": seongju.SEONGJU_MUNICIPALITY_NAME,
        "extra": {},
    }


def _community_menu(*, drift: bool = False) -> str:
    items = [
        ("공지사항", "/cnts/community/notice.html"),
        ("군민행복교육", "/cnts/community/educationApplication.html"),
        ("청소년문화의집", "/cnts/community/youthCulturalCenter.html"),
        ("신청서식", "/cnts/community/dataroom.html"),
        ("채용정보", "/cnts/community/job.html"),
        ("자주하는 질문", "/cnts/community/faq.html"),
        ("질문과 답변", "/cnts/community/qna.html"),
    ]
    if drift:
        items.insert(3, ("새 교육", "/cnts/community/newEducation.html"))
    return '<ul class="ul7">' + "".join(
        f'<li><a href="{href}">{label}</a></li>' for label, href in items
    ) + "</ul>"


def _headers(ledger: seongju.SeongjuLedger) -> str:
    labels = (
        "번호",
        "강좌명",
        "요일",
        "교육시간",
        ledger.period_label,
        "모집인원",
        "상태",
        "수강신청",
    )
    return "".join(f"<th>{label}</th>" for label in labels)


def _list_row(record: _Record) -> str:
    return f"""
      <tr>
        <td class="ea_num">{record.number}</td>
        <td class="ea_subject">{record.title}</td>
        <td class="ea_day"><span>{record.day}</span></td>
        <td class="ea_time">{record.time}</td>
        <td class="ea_period">{record.period}</td>
        <td class="ea_numPeople">{record.capacity}</td>
        <td class="ea_state"><span>{record.status}</span></td>
        <td class="ea_request"><a class="btn"
          href="?pg=vv&amp;class_seq={record.identity}&amp;page=1">수강신청</a></td>
      </tr>
    """


def _list_html(
    ledger: seongju.SeongjuLedger,
    records: tuple[_Record, ...],
    *,
    requested: int,
    total: int,
    last: int,
    menu_drift: bool = False,
) -> str:
    body = (
        '<tr><td colspan="8">등록된 강좌가 없습니다.</td></tr>'
        if total == 0 and not records
        else "".join(_list_row(record) for record in records)
    )
    return f"""
      <html><head><title>성주복지플랫폼</title></head><body>
        {_community_menu(drift=menu_drift)}
        <section class="pagetitle"><h1>{ledger.label}</h1>
          <p class="path">홈 &gt; 알림·문의 &gt; <strong>{ledger.label}</strong></p>
        </section>
        <section class="educationApplication"><div class="board_list">
          <div class="board_top"><div class="board_page">
            전체 <strong class="num1">{total}</strong>,
            <strong class="num2">{requested}</strong> /
            <strong class="num3">{last}</strong>페이지
          </div></div>
          <div class="board_body"><table>
            <caption>교육신청 목록</caption><thead><tr>{_headers(ledger)}</tr></thead>
            <tbody>{body}</tbody>
          </table></div>
        </div></section>
      </body></html>
    """


def _application_control(
    record: _Record,
    *,
    unsafe: bool = False,
) -> str:
    if record.status == "접수중":
        href = (
            "https://evil.example/apply"
            if unsafe
            else f"?pg=sign&amp;class_seq={record.identity}&amp;page=1"
        )
        return f'<a class="btn" href="{href}">수강신청</a>'
    function = "No_Signup_1()" if record.status == "접수마감" else "No_Signup()"
    return f'<a class="btn" href="#;" onclick="{function}">수강신청</a>'


def _detail_html(
    ledger: seongju.SeongjuLedger,
    record: _Record,
    *,
    title_override: str = "",
    status_override: str = "",
    unsafe_application: bool = False,
) -> str:
    shown = replace(
        record,
        title=title_override or record.title,
        status=status_override or record.status,
    )
    if ledger.key == "happiness":
        description = (
            f"-{shown.title}<br>-모집대상:{shown.target}<br>"
            f"-교육장소:{shown.venue}<br>"
            "-문의:010-1234-5678 teacher@example.org<br>"
            "신청 시 유의사항과 상세 자유 본문"
        )
    else:
        description = (
            "청소년들의 많은 신청 바랍니다.<br>"
            "문의 054-930-6883 youth@example.org"
        )
    material = (
        f"<tr><th>재료비</th><td>{shown.material_fee}</td></tr>"
        if shown.material_fee
        else ""
    )
    return f"""
      <html><head><title>성주복지플랫폼</title></head><body>
        <section class="pagetitle"><h1>{ledger.label}</h1></section>
        <section class="educationApplication"><div class="board_view"><table>
          <tbody>
            <tr><td class="itemtd" colspan="2"><span class="subject">{shown.title}</span></td></tr>
            <tr><th>요일 및 교육시간</th><td>{shown.day} {shown.time}</td></tr>
            <tr><th>{ledger.period_label}</th><td>{shown.period}</td></tr>
            <tr><th>모집인원</th><td>{shown.capacity}</td></tr>
            <tr><th>교육내용</th><td class="txt">{description}</td></tr>
            <tr><th>수강료</th><td>{shown.fee}</td></tr>
            {material}
            <tr><th>상태</th><td><span>{shown.status}</span></td></tr>
            <tr><th>수강신청</th><td>{_application_control(shown, unsafe=unsafe_application)}</td></tr>
          </tbody>
        </table></div></section>
      </body></html>
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
        detail_status_mismatch: bool = False,
        unsafe_application: bool = False,
    ) -> None:
        self.records = records
        self.menu_drift = menu_drift
        self.nonempty_sentinel = nonempty_sentinel
        self.detail_title_mismatch = detail_title_mismatch
        self.detail_status_mismatch = detail_status_mismatch
        self.unsafe_application = unsafe_application
        self.calls: list[str] = []
        self.detail_calls = 0

    def __call__(self, session: Any, url: str, timeout: int) -> _Response:
        del session, timeout
        self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        assert query.get("pg") != ["sign"]
        assert "check" not in parsed.path.lower()
        ledger = next(value for value in seongju.SEONGJU_LEDGERS if value.path == parsed.path)
        ledger_records = tuple(record for record in self.records if record.ledger == ledger.key)
        if query.get("pg") == ["vv"]:
            self.detail_calls += 1
            identity = query["class_seq"][0]
            record = next(value for value in ledger_records if value.identity == identity)
            return _Response(
                _detail_html(
                    ledger,
                    record,
                    title_override=(
                        "다른 상세 제목" if self.detail_title_mismatch else ""
                    ),
                    status_override=(
                        "접수마감"
                        if self.detail_status_mismatch and record.status != "접수마감"
                        else ""
                    ),
                    unsafe_application=self.unsafe_application,
                ),
                url,
            )
        page = int((query.get("page") or ["1"])[0] or "1")
        total = len(ledger_records)
        last = math.ceil(total / seongju.SEONGJU_PAGE_SIZE) if total else 0
        start = (page - 1) * seongju.SEONGJU_PAGE_SIZE
        selected = tuple(ledger_records[start : start + seongju.SEONGJU_PAGE_SIZE])
        if total and page > last:
            selected = (
                (ledger_records[0],)
                if self.nonempty_sentinel and ledger.key == "happiness"
                else ()
            )
        return _Response(
            _list_html(
                ledger,
                selected,
                requested=page,
                total=total,
                last=last,
                menu_drift=self.menu_drift,
            ),
            url,
        )


def _collect(fetcher: _Fetcher, *, today: date = date(2026, 7, 23), **kwargs: Any):
    session = _Session()
    rows, parser, meta = seongju.collect_seongju_education(
        _target(),
        today=today,
        session_factory=lambda: session,
        fetcher=fetcher,
        **kwargs,
    )
    assert session.closed is True
    return rows, parser, meta


def test_target_guard_candidate_and_owner_boundaries() -> None:
    assert seongju.is_seongju_education_target(_target())
    assert not seongju.is_seongju_education_target(
        _target(url=seongju.SEONGJU_REVIEW_URL)
    )
    assert not seongju.is_seongju_education_target(
        _target(provider=seongju.SEONGJU_REVIEW_PROVIDER)
    )
    assert not seongju.is_seongju_education_target(
        _target(url=seongju.SEONGJU_CANONICAL_URL + "?page=1")
    )
    assert seongju.SEONGJU_CANONICAL_CANDIDATE_ID == "MUNI_IR_6F852ACC73A5"
    assert seongju.SEONGJU_SOCIAL_WELFARE_PROVIDER == (
        "MUNI_WWW_SJWELFARE_OR_KR_9BB62674"
    )
    assert seongju.SEONGJU_SOCIAL_WELFARE_CANDIDATE_ID == "MUNI_IR_7A482D01A0AA"
    assert (
        seongju.SEONGJU_EXCLUDED_OWNER_BOUNDARIES[seongju.SEONGJU_REVIEW_URL]
        == "recycled_menu_id_now_job_board_not_education_or_reservation"
    )
    assert any("social_welfare_center" in value for value in seongju.SEONGJU_SEPARATE_OWNERS.values())


def test_complete_two_ledger_snapshot_binds_current_details_without_pii() -> None:
    fetcher = _Fetcher()
    rows, parser, meta = _collect(fetcher, max_pages=10, detail_limit=20)

    assert parser == seongju.SEONGJU_PARSER
    assert len(rows) == 12
    assert meta["full_snapshot_validated"] is True
    assert meta["ledger_pages"] == {"군민행복교육": 2, "청소년문화의집": 1}
    assert meta["ledger_counts"] == {"군민행복교육": 11, "청소년문화의집": 1}
    assert meta["ledger_current_counts"] == {"군민행복교육": 11, "청소년문화의집": 1}
    assert meta["sentinel_pages"] == {"happiness": 3, "youth": 2}
    assert meta["boundary_modes"] == {
        "happiness": "exact_structural_empty",
        "youth": "exact_structural_empty",
    }
    assert meta["source_status_counts"] == {
        "접수중": 1,
        "접수대기": 1,
        "접수마감": 10,
    }
    assert meta["current_status_counts"] == {
        "OPEN": 1,
        "SCHEDULED": 1,
        "CLOSED": 10,
    }
    assert meta["detail_verified"] == 12
    assert meta["application_control_count"] == 1
    assert meta["list_requests"] == 10
    assert meta["logical_requests"] == meta["physical_requests"] == 22
    assert fetcher.detail_calls == 12
    open_rows = [row for row in rows if row["status"] == "OPEN"]
    assert len(open_rows) == 1
    assert open_rows[0]["application_url"].endswith(
        f"pg=sign&class_seq={open_rows[0]['raw_fields']['identity']}&page=1"
    )
    assert open_rows[0]["reservation_available"] is True
    assert all(
        row["branch"] in {"군민행복교육", "성주군청소년문화의집"}
        for row in rows
    )
    assert {
        row["address"] for row in rows if row["branch"] == "군민행복교육"
    } == {
        "경상북도 성주군 성주읍 경산길 17",
        "경상북도 성주군 성주읍 성주로 3204",
    }
    serialized = repr(rows)
    assert "010-1234-5678" not in serialized
    assert "teacher@example.org" not in serialized
    assert "youth@example.org" not in serialized
    assert "상세 자유 본문" not in serialized
    assert all(parse_qs(urlparse(url).query).get("pg") != ["sign"] for url in fetcher.calls)


def test_complete_expired_snapshot_returns_proven_no_current_without_details() -> None:
    fetcher = _Fetcher()
    rows, _, meta = _collect(
        fetcher,
        today=date(2027, 1, 1),
        max_pages=10,
        detail_limit=0,
    )
    assert rows == []
    assert meta["source_total"] == 12
    assert meta["current_source_count"] == 0
    assert meta["expired_source_count"] == 12
    assert meta["detail_verified"] == 0
    assert meta["no_current_data"] is True
    assert "기준일 이전 종료" in meta["no_current_reason"]
    assert meta["full_snapshot_validated"] is True
    assert meta["logical_requests"] == meta["physical_requests"] == 10
    assert fetcher.detail_calls == 0


def test_zero_total_ledger_accepts_exact_empty_marker_and_stable_overflow() -> None:
    fetcher = _Fetcher(records=YOUTH)
    rows, _, meta = _collect(
        fetcher,
        today=date(2026, 7, 23),
        max_pages=10,
        detail_limit=1,
    )

    assert len(rows) == 1
    assert meta["ledger_counts"] == {"군민행복교육": 0, "청소년문화의집": 1}
    assert meta["ledger_pages"] == {"군민행복교육": 1, "청소년문화의집": 1}
    assert meta["sentinel_pages"] == {"happiness": 2, "youth": 2}
    assert meta["boundary_modes"]["happiness"] == (
        "exact_zero_total_first_and_overflow"
    )
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert all(parse_qs(urlparse(url).query).get("pg") != ["sign"] for url in fetcher.calls)


def test_menu_and_post_last_sentinel_drift_fail_closed() -> None:
    rows, _, meta = _collect(_Fetcher(menu_drift=True))
    assert rows == []
    assert "menu vocabulary changed" in meta["configured_collection_error"]

    rows, _, meta = _collect(_Fetcher(nonempty_sentinel=True))
    assert rows == []
    assert "post-last page" in meta["configured_collection_error"]
    assert meta["detail_pages"] == 0


def test_page_and_detail_caps_never_publish_partial_rows() -> None:
    rows, _, meta = _collect(_Fetcher(), max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "exceeds max_pages=1" in meta["configured_collection_error"]

    fetcher = _Fetcher()
    rows, _, meta = _collect(fetcher, detail_limit=11)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "partial current/future snapshot" in meta["configured_collection_error"]
    assert fetcher.detail_calls == 0


def test_detail_title_and_status_binding_fail_closed() -> None:
    rows, _, meta = _collect(_Fetcher(detail_title_mismatch=True))
    assert rows == []
    assert "list/detail title mismatch" in meta["configured_collection_error"]

    rows, _, meta = _collect(_Fetcher(detail_status_mismatch=True))
    assert rows == []
    assert "list/detail fields disagree" in meta["configured_collection_error"]


def test_unsafe_application_control_fails_and_form_is_never_fetched() -> None:
    fetcher = _Fetcher(unsafe_application=True)
    rows, _, meta = _collect(fetcher)
    assert rows == []
    assert "unsafe application form control" in meta["configured_collection_error"]
    assert meta["application_endpoint_requests"] == 0
    assert meta["identity_check_requests"] == 0
    assert meta["applicant_list_requests"] == 0
    assert all(parse_qs(urlparse(url).query).get("pg") != ["sign"] for url in fetcher.calls)


def test_contact_in_a_public_title_fails_closed() -> None:
    records = (replace(RECORDS[0], title="문의 010-9999-8888"), *RECORDS[1:])
    rows, _, meta = _collect(_Fetcher(records=records))
    assert rows == []
    assert "public row leaked contact data" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_SEONGJU_TESTS") != "1",
    reason="set RUN_LIVE_SEONGJU_TESTS=1 for the audited live snapshot",
)
def test_live_snapshot_2026_08_05() -> None:
    rows, parser, meta = seongju.collect_seongju_education(
        _target(),
        today=date(2026, 8, 5),
        max_pages=10,
        detail_limit=100,
        timeout=30,
    )
    assert parser == seongju.SEONGJU_PARSER
    assert rows == []
    assert meta["configured_collection_error"] == ""
    assert meta["full_snapshot_validated"] is True
    assert meta["ledger_pages"] == {"군민행복교육": 1, "청소년문화의집": 1}
    assert meta["ledger_counts"] == {"군민행복교육": 0, "청소년문화의집": 1}
    assert meta["ledger_current_counts"] == {"군민행복교육": 0, "청소년문화의집": 0}
    assert meta["source_total"] == 1
    assert meta["current_source_count"] == 0
    assert meta["expired_source_count"] == 1
    assert meta["source_status_counts"] == {"접수마감": 1}
    assert meta["detail_verified"] == 0
    assert meta["application_control_count"] == 0
    assert meta["application_endpoint_requests"] == 0
    assert meta["identity_check_requests"] == 0
    assert meta["applicant_list_requests"] == 0
    assert meta["sentinel_pages"] == {"happiness": 2, "youth": 2}
    assert meta["boundary_modes"] == {
        "happiness": "exact_zero_total_first_and_overflow",
        "youth": "exact_structural_empty",
    }
    assert meta["list_requests"] == 8
    assert meta["logical_requests"] == meta["physical_requests"] == 8
