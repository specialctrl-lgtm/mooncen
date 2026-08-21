from __future__ import annotations

from dataclasses import dataclass
from html import escape
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_daegu_suseong as suseong


@dataclass(frozen=True)
class Target:
    provider: str = suseong.DAEGU_SUSEONG_PROVIDER
    candidate_id: str = suseong.DAEGU_SUSEONG_CANDIDATE_ID
    url: str = suseong.DAEGU_SUSEONG_URL


class FakeResponse:
    def __init__(self, url: str, body: str, status_code: int = 200) -> None:
        self.url = url
        self.text = body
        self.content = body.encode("utf-8")
        self.status_code = status_code
        self.history: list[Any] = []
        self.headers = {"content-type": "text/html; charset=utf-8"}


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


_CENTRE_ROWS = (
    {
        "id": "CRS_000000090001",
        "title": "합성 센터 강좌",
        "branch": "고산",
        "detail_branch": "고산평생학습센터",
        "apply": "99.06.01 ~ 99.06.30",
        "period": "99.07.01 ~ 99.08.31",
        "application": "신청마감",
        "education": "교육중",
        "status": "신청마감 교육중",
        "cancelled": False,
    },
    {
        "id": "CRS_000000090002",
        "title": "합성 종료 강좌",
        "branch": "파동",
        "detail_branch": "파동평생학습센터",
        "apply": "90.01.01 ~ 90.01.10",
        "period": "90.02.01 ~ 90.02.28",
        "application": "신청마감",
        "education": "",
        "status": "신청마감",
        "cancelled": False,
    },
    {
        "id": "CRS_000000090003",
        "title": "합성 폐강 센터",
        "branch": "두산",
        "detail_branch": "두산평생학습센터",
        "apply": "99.06.01 ~ 99.06.30",
        "period": "99.07.01 ~ 99.08.31",
        "application": "",
        "education": "폐강",
        "status": "폐강",
        "cancelled": True,
    },
)

_HALL_ROWS = (
    {
        "id": "CRS_000000091001",
        "title": "합성 학습관 공개 강좌",
        "branch": "수성구 평생학습관",
        "detail_branch": "수성구 평생학습관",
        "apply": "99.07.01 ~ 99.07.31",
        "period": "99.08.01 ~ 99.09.01",
        "application": "신청하기",
        "education": "교육예정",
        "status": "신청하기 교육예정",
        "cancelled": False,
    },
    {
        "id": "CRS_000000091002",
        "title": "[폐강] 합성 학습관 강좌",
        "branch": "수성구 평생학습관",
        "detail_branch": "수성구 평생학습관",
        "apply": "99.06.01 ~ 99.06.30",
        "period": "99.08.01 ~ 99.09.01",
        "application": "신청마감",
        "education": "교육예정",
        "status": "신청마감 교육예정",
        "cancelled": True,
    },
)

_INFO_ROWS = {
    "coming": (
        {
            "id": "Yeyak_000000901",
            "title": "합성 정보화 예정 교육",
            "category": "정보화교육",
            "institution": "구청",
            "apply": "2099-08-01 ~ 2099-08-20",
            "period": "2099-09-01 ~ 2099-09-30",
            "status": "진행예정",
        },
    ),
    "open": (
        {
            "id": "Yeyak_000000567",
            "title": "주민정보화교육 접수 연습용",
            "category": "정보화교육",
            "institution": "구청",
            "apply": "2099-07-01 ~ 2099-12-30",
            "period": "2099-10-01 ~ 2099-10-31",
            "status": "진행중",
        },
    ),
    "closed": (
        {
            "id": "Yeyak_000000902",
            "title": "합성 종료 평생교육",
            "category": "평생학습",
            "institution": "구청",
            "apply": "2090-01-01 ~ 2090-01-10",
            "period": "2090-02-01 ~ 2090-02-28",
            "status": "진행완료",
        },
    ),
}


def _course_row(ledger: str, row: dict[str, Any], number: int) -> str:
    function = "fn_learning_details" if ledger == "learning_centres" else "fn_learningHall_details"
    title = escape(row["title"])
    branch = escape(row["branch"])
    common = f"""
      <td>{number}</td>
      <td class="td_title tal"><a href="javascript:;"
        onclick="{function}('{row["id"]}','','');return false;">
        <strong class="lecture">{title}</strong>
        <span class="educational">{branch}</span></a></td>
    """
    period = f"""
      <td><span class="period p1"><em class="sp_com">신청기간</em>
        {row["apply"]}<span class="group_p1">{row["application"]}</span></span>
        <span class="period p2"><em class="sp_com">교육기간</em>
        {row["period"]}<span class="group_p2">{row["education"]}</span></span>
        <span class="period p3"><em>요일 시간</em> 화 10:00~12:00</span></td>
    """
    tail = f"""
      <td>수강료 : 10,000원 / 재료비 : 무료</td>
      <td>온라인 : 1/10</td><td>접수방법 : 인터넷</td>
      <td class="td_state">{row["status"]}</td>
    """
    if ledger == "learning_centres":
        return f"<tr>{common}<td class='td_time'>시간 : 화 10:00~12:00</td>{period}{tail}</tr>"
    return f"<tr>{common}{period}{tail}</tr>"


def _course_page(
    ledger: str,
    rows: tuple[dict[str, Any], ...],
    *,
    page: int,
    full: bool = False,
) -> str:
    title = "강좌 및 수강신청" if ledger == "learning_centres" else "프로그램 신청"
    form = "icmsLearning" if ledger == "learning_centres" else "icmsLearningHall"
    function = "fn_learning_list" if ledger == "learning_centres" else "fn_learningHall_list"
    headers = (
        "번호",
        "강좌명 교육기관",
        "신청기간 교육기간",
        "수강료 재료비",
        "신청/모집",
        "접수방법",
        "상태",
    )
    body = "".join(_course_row(ledger, row, len(rows) - index) for index, row in enumerate(rows))
    current = "" if full or page > 1 else "<strong>1</strong>"
    return f"""
      <html><head><title>{title} | 수성구 평생교육 플랫폼 러닝톡</title></head>
      <body><form id="{form}" name="{form}">
        <table><thead><tr>{"".join(f"<th>{value}</th>" for value in headers)}</tr></thead>
          <tbody>{body}</tbody></table>
        <div class="pagination">{current}
          <a onclick="{function}(1,'');return false;">마지막</a></div>
      </form></body></html>
    """


def _course_detail(
    ledger: str,
    row: dict[str, Any],
    *,
    wrong_title: bool = False,
    wrong_identity: bool = False,
) -> str:
    form = "icmsLearningApply" if ledger == "learning_centres" else "icmsLearningHallApply"
    title_marker = "강좌 및 수강신청" if ledger == "learning_centres" else "프로그램 신청"
    identity = "CRS_000000099999" if wrong_identity else row["id"]
    start_apply, end_apply = row["apply"].split(" ~ ")
    start_period, end_period = row["period"].split(" ~ ")
    full_apply = f"20{start_apply.replace('.', '-')} 09:00 ~ 20{end_apply.replace('.', '-')} 18:00"
    full_period = f"20{start_period.replace('.', '-')} ~ 20{end_period.replace('.', '-')}"
    values = {
        "강좌명": "다른 강좌" if wrong_title else row["title"],
        "강좌분류": "일반강좌",
        "내용분류": "인문교양",
        "내용별 분류": "시민활동",
        "교육기관": row["detail_branch"],
        "교육대상": "| 수성구 주민",
        "모집인원": "온라인 : 10명 (현재 신청인원 : 1명)",
        "신청기간": full_apply,
        "교육기간": full_period,
        "교육시간": "요일: 화 시간: 10:00~12:00",
        "교육장소": "제1강의실",
        "수강료": "10,000 원",
        "재료비": "무료",
        "연령제한": "",
        "개인정보 동의": "SECRET_PRIVACY_TEXT 010-1111-2222",
        "접수방법": "인터넷 인터넷 접수조회",
        "강사": "SECRET_INSTRUCTOR private@example.test",
        "강좌소개": "SECRET_FREE_FORM",
        "강의계획서": "SECRET_ATTACHMENT.hwp",
        "문의전화": "053-111-2222",
        "신청상태": "신청중" if row["application"] == "신청하기" else "교육예정",
        "교육상태": "신청중" if row["application"] == "신청하기" else "교육예정",
        "오시는 길": "SECRET_MAP",
        "주소": "42000 대구광역시 수성구 합성로 1",
        "약도": "SECRET_MAP_IMAGE",
        "주의사항": "SECRET_NOTICE",
        "환불정책": "SECRET_REFUND",
    }
    if ledger == "learning_hall":
        values.pop("교육장소")
    table = "".join(f"<tr><th>{escape(key)}</th><td>{escape(value)}</td></tr>" for key, value in values.items())
    control = ""
    if row["application"] == "신청하기":
        function = "fn_apply_learning2" if ledger == "learning_centres" else "fn_apply_learningHall2"
        control = f'<a href="javascript:;" onclick="{function}();return false;">인터넷 접수신청</a>'
    secondary = f'<input type="hidden" name="edu_id" value="{identity}">' if ledger == "learning_hall" else ""
    return f"""
      <html><head><title>{title_marker} | 러닝톡</title></head><body>
        <form id="{form}" name="{form}">
          <input type="hidden" name="crsId" value="{identity}">{secondary}
          {control}<table class="tbl02">{table}</table>
        </form></body></html>
    """


def _info_page(partition: str, rows: tuple[dict[str, Any], ...], page: int) -> str:
    expected = {"coming": "진행예정", "open": "진행중", "closed": "진행완료"}[partition]
    body = "".join(
        f"""
        <tr><td>{len(rows) - index}</td><td>{row["category"]}</td>
          <td>{row["institution"]}</td><td><a
          href="javascript:viewPage('{row["id"]}');">{escape(row["title"])}</a></td>
          <td>{row["apply"]}</td><td>{expected}</td></tr>
        """
        for index, row in enumerate(rows)
    )
    current = "<strong>1</strong>" if page == 1 else ""
    return f"""
      <html><head><title>예약 | 수성구 예약서비스</title></head><body>
        <form id="yeyakVO" name="yeyakVO">
          <table id="bbsList"><thead><tr>
            <th>번호</th><th>카테고리</th><th>기관</th><th>제목</th>
            <th>신청기간</th><th>처리현황</th>
          </tr></thead><tbody>{body}</tbody></table>
          <div class="pagination">{current}<a
            onclick="fn_icms_navi_list(1,'');return false;">마지막</a></div>
        </form></body></html>
    """


def _info_detail(
    row: dict[str, Any],
    *,
    private_value: bool = False,
    wrong_identity: bool = False,
) -> str:
    identity = "Yeyak_000000999" if wrong_identity else row["id"]
    status = row["status"]
    control = '<a href="javascript:registerPage();">신청하기</a>' if status == "진행중" else ""
    pairs = {
        "제목": row["title"],
        "신청기간": row["apply"],
        "신청인원/모집인원": "2/20",
        "교육기간": row["period"],
        "장소": "수성구 교육장",
        "선정방식": "선착순",
        "선정자발표": "2099-08-21",
        "교육대상": "수성구 주민",
        "비용": "무료",
        "담당자": "SECRET_CONTACT 053-111-2222",
        "글내용": "SECRET_INFO_FREE_FORM private@example.test",
        "첨부파일목록": "SECRET_INFO_ATTACHMENT.hwp",
    }
    blocks = "".join(
        (
            f"<dl><dt>{escape(key)}</dt><dd>{escape(value)}"
            + (f"<span>{status}</span>" if key == "제목" else "")
            + "</dd></dl>"
        )
        for key, value in pairs.items()
    )
    secret = "SECRET_APPLICANT" if private_value else ""
    return f"""
      <html><head><title>예약 | 수성구 예약서비스</title></head><body>
        <form id="yeyakDetailVO"><input name="yeyak_id" value="{identity}">
          <input name="name" value="{secret}"></form>
        <form id="yeyakVO">{control}
          <div id="inputMyInfo"><div id="bbsView"><input id="mobile_middle" value="{secret}"></div></div>
          <div id="bbsView">{blocks}</div>
        </form>
        <script>function registerPage(){{ location='addYeyak.do&yeyak_id={identity}'; }}</script>
      </body></html>
    """


class Backend:
    def __init__(
        self,
        *,
        stable_drift: bool = False,
        complete_drop: bool = False,
        sentinel_data: bool = False,
        duplicate_identity: bool = False,
        wrong_detail_title: bool = False,
        wrong_application_identity: bool = False,
        private_value: bool = False,
        retry_once: bool = False,
    ) -> None:
        self.stable_drift = stable_drift
        self.complete_drop = complete_drop
        self.sentinel_data = sentinel_data
        self.duplicate_identity = duplicate_identity
        self.wrong_detail_title = wrong_detail_title
        self.wrong_application_identity = wrong_application_identity
        self.private_value = private_value
        self.retry_once = retry_once
        self.failed_once = False
        self.urls: list[str] = []
        self.page_one_calls = {"learning_centres": 0, "learning_hall": 0}
        self.sessions: list[FakeSession] = []

    def session(self) -> FakeSession:
        current = FakeSession()
        self.sessions.append(current)
        return current

    def fetch(self, _session: Any, url: str, _timeout: int) -> FakeResponse:
        self.urls.append(url)
        if self.retry_once and not self.failed_once:
            self.failed_once = True
            return FakeResponse(url, "temporary", status_code=503)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        menu_link = (query.get("menu_link") or [""])[0]
        page = int((query.get("pageIndex") or ["1"])[0])
        if parsed.hostname == suseong.DAEGU_SUSEONG_HOST:
            ledger = "learning_hall" if "learningHall" in menu_link else "learning_centres"
            source = list(_HALL_ROWS if ledger == "learning_hall" else _CENTRE_ROWS)
            if self.duplicate_identity and ledger == "learning_hall":
                source[1] = {**source[1], "id": source[0]["id"]}
            if menu_link.endswith("list.do"):
                if page == 1:
                    self.page_one_calls[ledger] += 1
                    if self.stable_drift and self.page_one_calls[ledger] >= 3:
                        source[0] = {**source[0], "title": "변경된 경계 강좌"}
                if page == -1 and self.complete_drop:
                    source = source[:-1]
                if page > 1 and not self.sentinel_data:
                    source = []
                return FakeResponse(
                    url,
                    _course_page(ledger, tuple(source), page=page, full=page == -1),
                )
            if menu_link.endswith("details.do"):
                identity = (query.get("crsId") or [""])[0]
                rows = {row["id"]: row for row in (*_CENTRE_ROWS, *_HALL_ROWS)}
                row = rows.get(identity)
                if row is None:
                    return FakeResponse(url, "missing", status_code=404)
                return FakeResponse(
                    url,
                    _course_detail(
                        ledger,
                        row,
                        wrong_title=self.wrong_detail_title and identity.endswith("1001"),
                        wrong_identity=(self.wrong_application_identity and identity.endswith("1001")),
                    ),
                )
        if parsed.hostname == suseong.DAEGU_SUSEONG_DISTRICT_HOST:
            if menu_link.endswith("yeyakList.do"):
                partition = (query.get("searchStatus") or [""])[0]
                rows = list(_INFO_ROWS.get(partition, ()))
                if page > 1 and not self.sentinel_data:
                    rows = []
                return FakeResponse(url, _info_page(partition, tuple(rows), page))
            if menu_link.endswith("yeyakView.do"):
                identity = (query.get("yeyak_id") or [""])[0]
                rows = {row["id"]: row for partition_rows in _INFO_ROWS.values() for row in partition_rows}
                row = rows.get(identity)
                if row is None:
                    return FakeResponse(url, "missing", status_code=404)
                return FakeResponse(
                    url,
                    _info_detail(
                        row,
                        private_value=self.private_value,
                        wrong_identity=(self.wrong_application_identity and identity.endswith("0567")),
                    ),
                )
        return FakeResponse(url, "missing", status_code=404)


def _collect(backend: Backend, **kwargs: Any):
    return suseong.collect_daegu_suseong_education(
        Target(),
        timeout=1,
        max_pages=kwargs.pop("max_pages", 10),
        detail_limit=kwargs.pop("detail_limit", 20),
        max_requests=kwargs.pop("max_requests", 100),
        source_limit=kwargs.pop("source_limit", 100),
        max_workers=kwargs.pop("max_workers", 4),
        fetch_attempts=kwargs.pop("fetch_attempts", 1),
        today=kwargs.pop("today", "2099-07-22"),
        fetcher=backend.fetch,
        session_factory=backend.session,
        sleeper=lambda _seconds: None,
        **kwargs,
    )


def test_exact_target_ids_urls_and_owner_boundaries() -> None:
    assert suseong.DAEGU_SUSEONG_PROVIDER == "MUNI_LLL_SUSEONG_KR_2C82AF9F"
    assert suseong.DAEGU_SUSEONG_CANDIDATE_ID == "MUNI_IR_0918A27B489E"
    assert suseong.is_target(Target()) is True
    assert suseong.is_target(Target(provider="WRONG")) is False
    assert suseong.is_target(Target(candidate_id="WRONG")) is False
    assert suseong.is_target(Target(url=suseong.DAEGU_SUSEONG_URL + "&x=1")) is False
    assert suseong.daegu_suseong_detail_url("learning_centres", "CRS_000000090001").endswith("crsId=CRS_000000090001")
    assert suseong.daegu_suseong_detail_url("learning_centres", "bad") == ""
    assert suseong.daegu_suseong_info_detail_url("Yeyak_000000901").endswith("yeyak_id=Yeyak_000000901")
    assert (
        suseong.DAEGU_SUSEONG_CANDIDATE_AUDIT["MUNI_IR_2BD0606E8578"]["existing_owner"]
        == "MUNI_LLL_SUSEONG_KR_C40B81D9"
    )
    assert (
        suseong.DAEGU_SUSEONG_EXCLUDED_SCOPE["daegu_city_aggregate"]["reason"]
        == "separate_citywide_aggregate_owner_DAEGU_RESERVATION"
    )


def test_managed_session_scopes_large_response_allowance_to_suseong() -> None:
    current = suseong.daegu_suseong_session_factory()
    try:
        assert (
            current.max_response_bytes
            == suseong.DAEGU_SUSEONG_MANAGED_MAX_RESPONSE_BYTES
        )
        assert current.total_timeout_seconds == 120
    finally:
        current.close()


def test_complete_ledgers_sentinels_details_statuses_suppression_and_privacy() -> None:
    backend = Backend()
    rows, parser, meta = _collect(backend)

    assert parser == suseong.DAEGU_SUSEONG_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["source_total"] == meta["source_rows"] == 8
    assert meta["source_rows_by_ledger"] == {
        "learning_centres": 3,
        "learning_hall": 2,
        "district_reservation": 3,
    }
    assert meta["source_current_rows_by_ledger"] == {
        "learning_centres": 2,
        "learning_hall": 2,
        "district_reservation": 2,
    }
    assert meta["returned_rows_by_ledger"] == {
        "learning_centres": 1,
        "learning_hall": 1,
        "district_reservation": 1,
    }
    assert meta["declared_pages_by_ledger"] == {
        "learning_centres": 1,
        "learning_hall": 1,
        "district_reservation": {"coming": 1, "open": 1, "closed": 1},
    }
    assert meta["pages"] == 5
    assert meta["list_requests"] == 24
    assert meta["complete_list_requests"] == 2
    assert meta["sentinel_requests"] == 5
    assert meta["stability_rechecks"] == 10
    assert meta["detail_attempts"] == meta["detail_pages"] == 7
    assert meta["network_requests"] == 31
    assert meta["retry_count"] == 0
    assert meta["current_count"] == 6
    assert meta["expired_count"] == 2
    assert meta["suppressed_cancelled_rows"] == 2
    assert meta["suppressed_practice_rows"] == 1
    assert meta["suppressed_nonproduction_rows"] == 3
    assert meta["returned_count"] == len(rows) == 3
    assert meta["status_counts"] == {"CLOSED": 1, "OPEN": 1, "SCHEDULED": 1}
    assert meta["application_control_count"] == 1
    assert meta["duplicate_source_rows"] == 0
    assert meta["semantic_duplicate_rows"] == 0
    assert meta["application_pages_requested"] == 0
    assert meta["applicant_result_pages_requested"] == 0
    assert meta["pii_payload_persisted"] is False

    by_ledger = {row["raw_fields"]["ledger"]: row for row in rows}
    assert set(by_ledger) == {
        "learning_centres",
        "learning_hall",
        "district_reservation",
    }
    assert by_ledger["learning_centres"]["branch"] == "고산평생학습센터"
    assert by_ledger["learning_hall"]["reservation_available"] is True
    assert by_ledger["learning_hall"]["application_url"].endswith("crsId=CRS_000000091001")
    assert by_ledger["district_reservation"]["status"] == "SCHEDULED"
    assert all(row["municipality_code"] == "2726000000" for row in rows)
    rendered = repr(rows)
    for secret in (
        "SECRET_PRIVACY_TEXT",
        "SECRET_INSTRUCTOR",
        "SECRET_FREE_FORM",
        "SECRET_ATTACHMENT",
        "SECRET_CONTACT",
        "SECRET_INFO_FREE_FORM",
        "SECRET_INFO_ATTACHMENT",
        "private@example.test",
        "010-1111-2222",
    ):
        assert secret not in rendered
    assert not any("addYeyak.do" in url or "myList.do" in url or "applyLearning" in url for url in backend.urls)
    assert all(current.closed for current in backend.sessions)


@pytest.mark.parametrize(
    ("flag", "needle"),
    (
        ("stable_drift", "stable"),
        ("complete_drop", "edge mismatch"),
        ("sentinel_data", "sentinel"),
        ("duplicate_identity", "duplicate source identity"),
        ("wrong_detail_title", "heading mismatch"),
        ("wrong_application_identity", "identity mismatch"),
        ("private_value", "private lookup payload"),
    ),
)
def test_any_contract_failure_discards_the_whole_snapshot(flag: str, needle: str) -> None:
    rows, _parser, meta = _collect(Backend(**{flag: True}))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert needle in meta["configured_collection_error"].casefold()


def test_caps_dedupe_retry_and_wrong_target_fail_closed() -> None:
    rows, _parser, meta = _collect(Backend(), max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(Backend(), detail_limit=6)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(Backend(), source_limit=7)
    assert rows == []
    assert meta["source_cap_reached"] is True

    rows, _parser, meta = _collect(Backend(), dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]

    backend = Backend(retry_once=True)
    rows, _parser, meta = _collect(backend, fetch_attempts=2)
    assert len(rows) == 3
    assert meta["network_requests"] == 32
    assert meta["retry_count"] == 1

    backend = Backend()
    rows, _parser, meta = suseong.collect_daegu_suseong_education(
        Target(provider="WRONG"),
        fetcher=backend.fetch,
        session_factory=backend.session,
    )
    assert rows == []
    assert backend.urls == []
    assert "exact canonical" in meta["configured_collection_error"]


def test_explicit_no_current_snapshot_is_success_not_scrape_failure() -> None:
    rows, _parser, meta = _collect(Backend(), today="2100-01-01")
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 8
    assert meta["detail_attempts"] == meta["detail_pages"] == 3
    assert meta["current_count"] == meta["returned_count"] == 0
    assert meta["expired_count"] == 8
    assert meta["no_current_data"] is True
    assert "no current/future production course" in meta["no_current_reason"]


@pytest.mark.skipif(
    os.getenv("MOONCEN_RUN_DAEGU_SUSEONG_LIVE") != "1",
    reason="set MOONCEN_RUN_DAEGU_SUSEONG_LIVE=1 for the exact 436-request audit",
)
def test_live_exact_snapshot_matches_2026_07_22_audit() -> None:
    rows, parser, meta = suseong.collect_daegu_suseong_education(
        Target(),
        timeout=60,
        max_pages=1500,
        detail_limit=500,
        max_requests=600,
        max_workers=8,
        today="2026-07-22",
    )
    assert parser == suseong.DAEGU_SUSEONG_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 14649
    assert meta["source_rows_by_ledger"] == {
        "learning_centres": 13758,
        "learning_hall": 890,
        "district_reservation": 1,
    }
    assert meta["declared_pages_by_ledger"] == {
        "learning_centres": 1376,
        "learning_hall": 89,
        "district_reservation": {"coming": 1, "open": 1, "closed": 1},
    }
    assert meta["current_count"] == 412
    assert meta["expired_count"] == 14237
    assert meta["suppressed_cancelled_rows"] == 15
    assert meta["suppressed_practice_rows"] == 1
    assert meta["audited_application_date_anomalies"] == 2
    assert meta["audited_education_date_anomalies"] == 13
    assert meta["returned_count"] == len(rows) == 396
    assert meta["status_counts"] == {"CLOSED": 387, "OPEN": 8, "SCHEDULED": 1}
    assert meta["branch_counts"] == {
        "고산평생학습센터": 122,
        "만촌평생학습센터": 63,
        "수성동평생학습센터": 50,
        "지산평생학습센터": 50,
        "두산평생학습센터": 48,
        "파동평생학습센터": 44,
        "수성구 평생학습관": 19,
    }
    assert meta["application_control_count"] == 8
    assert meta["list_requests"] == 24
    assert meta["detail_attempts"] == meta["detail_pages"] == 412
    assert meta["network_requests"] == 436
    assert meta["retry_count"] == 0
    assert all(row["municipality_code"] == "2726000000" for row in rows)
