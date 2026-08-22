from __future__ import annotations

from dataclasses import dataclass
from html import escape
import os
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_yangsan as yangsan


class Response:
    def __init__(self, url: str, html: str) -> None:
        self.url = url
        self.status_code = 200
        self.history: list[object] = []
        self.headers = {"Content-Type": "text/html; charset=UTF-8"}
        self.content = html.encode()


class Session:
    def close(self) -> None:
        pass


def _options(items: tuple[tuple[str, str], ...]) -> str:
    return "".join(f'<option value="{escape(code)}">{escape(label)}</option>' for code, label in items)


def _lifelong_list(edu_type: str, page: int, *, bad_sentinel: bool = False) -> str:
    label = {"1": "학습관교육", "6": "행복학습센터"}[edu_type]
    identity = {"1": "101", "6": "601"}[edu_type]
    venue = "양산시 평생학습관" if edu_type == "1" else "사송트루엘 행복학습센터"
    cards = (
        f'''<li><a href="javascript:void(0);" data-target="layerpopup_mycode"
        onclick="fn_popup_open_totalLecture({identity},{edu_type},'Y');">
        <em class="badge">{label}</em><p class="subj">강좌 {edu_type}</p>
        <ul class="info"><li><strong>교육장소</strong><span>{venue}</span></li>
        <li><strong>교육기간</strong><span>2026-08-01 ~ 2026-09-01</span></li>
        <li><strong>교육시간</strong><span>화 / 10:30 ~ 12:00</span></li></ul>
        <span class="state">접수마감</span></a></li>'''
        if page == 1
        else '<li class="no-data"><span class="no">changed</span></li>'
        if bad_sentinel
        else '<li class="no-data"><span class="no">등록된 데이터가 없습니다.</span></li>'
    )
    return f'''<form id="listForm" name="listForm" method="post"
      action="/edu/forever/lecture/search.do?mid={yangsan.YANGSAN_MID}">
      <input name="page" value="{page}"><input name="currentPageNo" value="1">
      <input name="eduType" value="{edu_type}"><input name="keyword" value="">
      <select name="recordCountPerPage"><option value="12"></option><option value="16"></option><option value="24"></option></select>
      <div class="bod_head">전체 1 건</div><div class="bod_cardList"><ul class="clFix">{cards}</ul></div>
      </form>'''


def _field_table(fields: list[tuple[str, str]], classes: str = "tbl detail") -> str:
    rows = "".join(f"<tr><th>{escape(k)}</th><td>{escape(v)}</td></tr>" for k, v in fields)
    return f'<table class="{classes}"><tbody>{rows}</tbody></table>'


def _lifelong_detail(edu_type: str, *, bad_title: bool = False) -> str:
    if edu_type == "1":
        fields = [
            ("교육분야", "인문"), ("교육구분", "일반"),
            ("교육시간", "2026-08-01 10:30 ~ 2026-09-01 12:00 (화)"),
            ("교육대상", "성인"), ("1차접수기간", "2026-07-01 ~ 2026-07-15"),
            ("2차접수기간", ""), ("수강료", "무료"), ("재료비(기타비용)", "무료"),
            ("모집형태", "온라인"), ("정원/신청/확정", "10/2/2"),
            ("교육장소", "양산시 평생학습관"), ("문의처", "055-000-0000"),
            ("교육내용", "discard me"), ("강의계획서", "plan.pdf"),
        ]
        identity = "101"
    else:
        fields = [
            ("교육분야", "인문"), ("강사명", "discard"), ("교육내용", "discard"),
            ("프로그램 기간", "2026-08-01 10:30 ~ 2026-09-01 12:00 (화)"),
            ("접수 기간", "2026-07-01 ~ 2026-07-15"), ("교육대상", "성인"),
            ("연령제한", "19세~"), ("수강정원", "10명"), ("강좌횟수/시수", "4회/8시간"),
            ("수강료", "무료"), ("재료비", "무료"), ("강의장소", "사송트루엘 행복학습센터"),
            ("센터명", "사송트루엘 행복학습센터"), ("문의처", "055-000-0000"),
            ("강의계획서", "plan.pdf"),
        ]
        identity = "601"
    title = "changed" if bad_title else f"강좌 {edu_type}"
    return f'<div class="pop-tit"><h3>{title}</h3><span data-state="접수마감"></span></div><input name="idx" value="{identity}"><input name="selectedEduType" value="{edu_type}">{_field_table(fields)}'


def _booking_form(page: int, body: str) -> str:
    return f'''<form id="list" name="list" method="post" action="/booking/lecture/list.do?mid={yangsan.YANGSAN_MID}">
    <input name="page" value="{page}"><input name="lecStartDt" value=""><input name="lecEndDt" value="">
    <input name="appStartDt" value=""><input name="appEndDt" value=""><input name="searchTxt" value="">
    <select name="orgIdx">{_options(yangsan.YANGSAN_BOOKING_ORG_REGISTRY)}</select>
    <select name="lecStateType">{_options(yangsan.YANGSAN_BOOKING_STATE_REGISTRY)}</select>
    <select name="lecType">{_options(yangsan.YANGSAN_BOOKING_TYPE_REGISTRY)}</select>{body}</form>'''


_HEADERS = ("번호", "강좌명", "교육장소", "교육기간", "온라인(정원/접수/대기) 오프라인(정원/접수)", "수강대상", "상태", "강사명", "접수기간", "교육시간", "수강료")


def _booking_list(
    page: int,
    *,
    bad_branch: bool = False,
    identity: str = "701",
    source_status: str = "접수마감",
    event_period: str = "2026-08-01 ~ 2026-09-01",
    application_period: str = "2026-07-01 ~ 2026-07-15",
) -> str:
    head = "".join(f"<th>{value}</th>" for value in _HEADERS)
    rows = ""
    if page == 1:
        branch = "unknown" if bad_branch else "증산다누리터"
        detail = yangsan.yangsan_booking_detail_url(identity)
        rows = f'''<tr><td rowspan="2">1</td><td><span class="bk">{branch}</span>예약 강좌</td><td>강의실</td>
        <td>{escape(event_period)}</td><td>10/2/0 0/0</td><td>성인</td><td><a href="{detail}">{escape(source_status)}</a></td></tr>
        <tr><td>discard</td><td>{escape(application_period)}</td><td>화 10:30 ~ 12:00</td><td>무료</td></tr>'''
    table = f'<div class="tbl-box"><table class="tbl taC"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>'
    return _booking_form(page, table)


def _booking_detail(
    *,
    bad_application: bool = False,
    identity: str = "701",
    event_period: str = "2026-08-01 ~ 2026-09-01",
    application_period: str = "2026-07-01 ~ 2026-07-15",
) -> str:
    fields = [
        ("강좌명", "예약 강좌"), ("강사명", "discard"), ("접수기간", application_period),
        ("교육기간", event_period), ("교육시간", "10:30 ~ 12:00"),
        ("총수강료", "무료"), ("재료비", "무료"), ("교육장소", "강의실"),
        ("교육대상", "성인"), ("문의처", "055-000-0000"), ("모집인원", "10"),
        ("모집현황", "2"), ("강좌설명", "discard"), ("결제방법", "없음"), ("안내계좌", "discard"),
    ]
    application_identity = "999" if bad_application else identity
    return _field_table(fields, "tbl") + f'<form id="apply" name="apply" method="post" action="/booking/lecture/app/write.do?lecIdx={application_identity}&amp;mid={yangsan.YANGSAN_MID}"></form>'


@dataclass
class Fixture:
    bad_lifelong_sentinel: bool = False
    bad_lifelong_title: bool = False
    bad_booking_branch: bool = False
    bad_application: bool = False
    booking_identity: str = "701"
    booking_status: str = "접수마감"
    booking_event_period: str = "2026-08-01 ~ 2026-09-01"
    booking_application_period: str = "2026-07-01 ~ 2026-07-15"

    def fetch(self, _session: object, method: str, url: str, *, timeout: int, data: dict[str, str]) -> Response:
        del timeout
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == yangsan.YANGSAN_LIFELONG_LIST_PATH:
            return Response(url, _lifelong_list(query["eduType"][0], int(query["page"][0]), bad_sentinel=self.bad_lifelong_sentinel))
        if parsed.path in {yangsan.YANGSAN_FOREVER_DETAIL_PATH, yangsan.YANGSAN_HAPPINESS_DETAIL_PATH}:
            assert method == "POST" and set(data) == {"idx", "eduType"}
            return Response(url, _lifelong_detail(data["eduType"], bad_title=self.bad_lifelong_title))
        if parsed.path == yangsan.YANGSAN_BOOKING_LIST_PATH:
            return Response(
                url,
                _booking_list(
                    int(query["page"][0]),
                    bad_branch=self.bad_booking_branch,
                    identity=self.booking_identity,
                    source_status=self.booking_status,
                    event_period=self.booking_event_period,
                    application_period=self.booking_application_period,
                ),
            )
        if parsed.path == yangsan.YANGSAN_BOOKING_DETAIL_PATH:
            return Response(
                url,
                _booking_detail(
                    bad_application=self.bad_application,
                    identity=self.booking_identity,
                    event_period=self.booking_event_period,
                    application_period=self.booking_application_period,
                ),
            )
        raise AssertionError(f"unsafe request {method} {url}")


def _target(provider: str, url: str) -> dict[str, str]:
    return {"provider": provider, "url": url}


def test_lifelong_complete_two_partition_snapshot() -> None:
    fixture = Fixture()
    rows, parser, meta = yangsan.collect_yangsan_lifelong(
        _target(yangsan.YANGSAN_LIFELONG_PROVIDER, yangsan.YANGSAN_LIFELONG_CANONICAL_URL),
        today="2026-07-23", session_factory=Session, fetcher=fixture.fetch,
    )
    assert parser == yangsan.YANGSAN_LIFELONG_PARSER
    assert len(rows) == 2
    assert {row["branch"] for row in rows} == {"양산시 평생학습관", "사송트루엘 행복학습센터"}
    assert len({row["provider_course_id"] for row in rows}) == 2
    assert meta["source_total"] == 2 and meta["snapshot_complete"] is True
    assert meta["pii_values_persisted"] == meta["application_endpoint_requests"] == 0


def test_booking_complete_snapshot_and_legacy_match() -> None:
    fixture = Fixture()
    assert yangsan.is_yangsan_booking_target(_target(yangsan.YANGSAN_BOOKING_PROVIDER, yangsan.YANGSAN_BOOKING_LEGACY_URL))
    rows, parser, meta = yangsan.collect_yangsan_booking(
        _target(yangsan.YANGSAN_BOOKING_PROVIDER, yangsan.YANGSAN_BOOKING_CANONICAL_URL),
        today="2026-07-23", session_factory=Session, fetcher=fixture.fetch,
    )
    assert parser == yangsan.YANGSAN_BOOKING_PARSER and len(rows) == 1
    assert rows[0]["branch"] == "증산다누리터" and rows[0]["application_url"] == ""
    assert meta["source_total"] == 1 and meta["snapshot_complete"] is True
    assert meta["application_endpoint_requests"] == meta["attachment_endpoint_requests"] == 0


@pytest.mark.parametrize(
    ("source_status", "expected_status", "reservation_available"),
    [
        ("접수전", "SCHEDULED", False),
        ("접수중", "OPEN", True),
        ("대기자 접수 중", "OPEN", True),
        ("정원마감", "CLOSED", False),
    ],
)
def test_booking_current_statuses_bind_application_control(
    source_status: str,
    expected_status: str,
    reservation_available: bool,
) -> None:
    fixture = Fixture(booking_status=source_status)
    rows, _, meta = yangsan.collect_yangsan_booking(
        _target(
            yangsan.YANGSAN_BOOKING_PROVIDER,
            yangsan.YANGSAN_BOOKING_CANONICAL_URL,
        ),
        today="2026-07-23",
        session_factory=Session,
        fetcher=fixture.fetch,
    )

    assert len(rows) == 1
    assert rows[0]["status"] == expected_status
    assert rows[0]["reservation_available"] is reservation_available
    assert bool(rows[0]["application_url"]) is reservation_available
    assert meta["snapshot_complete"] is True


@pytest.mark.parametrize(
    "fixture",
    [
        Fixture(
            booking_identity="754",
            booking_status="접수전",
            booking_application_period=(
                "2026.08.18 ~ 2026.05.15 09:00 ~ 15:00"
            ),
        ),
        Fixture(
            booking_identity="752",
            booking_status="접수전",
            booking_event_period="2026.09.07 ~ 2026.08.31",
        ),
    ],
)
def test_exact_audited_booking_period_anomalies_are_excluded(
    fixture: Fixture,
) -> None:
    rows, _, meta = yangsan.collect_yangsan_booking(
        _target(
            yangsan.YANGSAN_BOOKING_PROVIDER,
            yangsan.YANGSAN_BOOKING_CANONICAL_URL,
        ),
        today="2026-07-23",
        session_factory=Session,
        fetcher=fixture.fetch,
    )

    assert rows == []
    assert meta["source_total"] == 1
    assert meta["current_source_count"] == 1
    assert meta["publishable_current_source_count"] == 0
    assert meta["audited_malformed_source_count"] == 1
    assert meta["detail_pages"] == 0
    assert meta["snapshot_complete"] is True


@pytest.mark.parametrize(
    ("collector", "fixture", "fragment"),
    [
        ("lifelong", Fixture(bad_lifelong_sentinel=True), "sentinel"),
        ("lifelong", Fixture(bad_lifelong_title=True), "title/status drift"),
        ("booking", Fixture(bad_booking_branch=True), "unregistered institution"),
        ("booking", Fixture(bad_application=True), "application identity drift"),
        (
            "booking",
            Fixture(
                booking_application_period=(
                    "2026.08.18 ~ 2026.05.15 09:00 ~ 15:00"
                )
            ),
            "reversed application period",
        ),
    ],
)
def test_contract_drift_fails_closed(collector: str, fixture: Fixture, fragment: str) -> None:
    if collector == "lifelong":
        target = _target(yangsan.YANGSAN_LIFELONG_PROVIDER, yangsan.YANGSAN_LIFELONG_CANONICAL_URL)
        rows, _, meta = yangsan.collect_yangsan_lifelong(target, today="2026-07-23", session_factory=Session, fetcher=fixture.fetch)
    else:
        target = _target(yangsan.YANGSAN_BOOKING_PROVIDER, yangsan.YANGSAN_BOOKING_CANONICAL_URL)
        rows, _, meta = yangsan.collect_yangsan_booking(target, today="2026-07-23", session_factory=Session, fetcher=fixture.fetch)
    assert rows == [] and fragment in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_targets_caps_and_three_source_namespaces() -> None:
    assert not yangsan.is_yangsan_lifelong_target(_target(yangsan.YANGSAN_LIFELONG_PROVIDER, "http://www.yangsan.go.kr/edu/forever/lecture/search.do?mid=0301000000"))
    ids = {
        yangsan.yangsan_source_identity(yangsan.YANGSAN_LIFELONG_PROVIDER, "forever_1", "1"),
        yangsan.yangsan_source_identity(yangsan.YANGSAN_LIFELONG_PROVIDER, "forever_6", "1"),
        yangsan.yangsan_source_identity(yangsan.YANGSAN_BOOKING_PROVIDER, "booking", "1"),
    }
    assert len(ids) == 3 and yangsan.YANGSAN_AUDITED_SOURCE_TOTALS["all"] == 846
    rows, _, meta = yangsan.collect_yangsan_booking(
        _target(yangsan.YANGSAN_BOOKING_PROVIDER, yangsan.YANGSAN_BOOKING_CANONICAL_URL),
        today="2026-07-23", max_pages=2, session_factory=Session, fetcher=Fixture().fetch,
    )
    assert rows == [] and meta["source_cap_reached"] is True


@pytest.mark.skipif(os.environ.get("RUN_YANGSAN_LIVE") != "1", reason="opt-in live audit")
def test_live_yangsan_audited_snapshot() -> None:
    lifelong, _, a = yangsan.collect_yangsan_lifelong(
        _target(yangsan.YANGSAN_LIFELONG_PROVIDER, yangsan.YANGSAN_LIFELONG_CANONICAL_URL), today="2026-07-23"
    )
    booking, _, b = yangsan.collect_yangsan_booking(
        _target(yangsan.YANGSAN_BOOKING_PROVIDER, yangsan.YANGSAN_BOOKING_CANONICAL_URL), today="2026-07-23"
    )
    assert len(lifelong) == 13 and a["status_counts"] == {"CLOSED": 8, "OPEN": 5}
    assert len(booking) == 100 and b["branch_counts"] == {
        "반려동물지원센터(교육)": 5, "증산다누리터": 15, "물금읍행정복지센터": 14,
        "동면행정복지센터": 42, "양주동행정복지센터": 19, "양산시농업기술센터": 5,
    }
    assert b["audited_malformed_current_count"] == 2
