from __future__ import annotations

import os
from typing import Any

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_jeju_city as jj
from tools.promote_municipal_integrated_reservation_targets import (
    candidate_id,
    normalized_duplicate_url,
    stable_provider,
)


TARGETS = jj.JEJU_EXECUTING_TARGETS


@pytest.mark.parametrize("provider,url", TARGETS)
def test_exact_canonical_owner_targets(provider: str, url: str) -> None:
    assert jj.is_jeju_city_education_target({"provider": provider, "url": url})
    assert not jj.is_jeju_city_education_target(
        {"provider": provider, "url": url + "#fragment"}
    )
    assert not jj.is_jeju_city_education_target(
        {"provider": provider, "url": url.replace("https://", "http://")}
    )
    assert not jj.is_jeju_city_education_target(
        {"provider": provider, "url": url.replace("https://", "https://u:p@")}
    )


@pytest.mark.parametrize("provider,url", TARGETS)
def test_production_collection_requires_managed_session(provider: str, url: str) -> None:
    rows, parser, meta = jj.collect(
        {"provider": provider, "url": url}, today="2026-07-23"
    )
    assert rows == []
    assert parser == jj.JEJU_CITY_PARSER
    assert meta["snapshot_complete"] is False
    assert meta["application_endpoints_called"] == 0
    assert meta["applicant_endpoints_called"] == 0
    assert "session_factory" in meta["configured_collection_error"]


def test_provider_and_candidate_ids_follow_repository_url_hashes() -> None:
    candidates = {
        jj.JEJU_INTEGRATED_PROVIDER: jj.JEJU_INTEGRATED_CANDIDATE_ID,
        jj.JEJU_LIFELONG_PROVIDER: jj.JEJU_LIFELONG_CANDIDATE_ID,
        jj.JEJU_YOUTH_PROVIDER: jj.JEJU_YOUTH_CANDIDATE_ID,
        jj.JEJU_RESIDENT_PROVIDER: jj.JEJU_RESIDENT_CANDIDATE_ID,
        jj.JEJU_LIBRARY_PROVIDER: jj.JEJU_LIBRARY_CANDIDATE_ID,
        jj.JEJU_AGRICULTURE_PROVIDER: jj.JEJU_AGRICULTURE_CANDIDATE_ID,
        jj.JEJU_STAR_PROVIDER: jj.JEJU_STAR_CANDIDATE_ID,
        jj.JEJU_DREAM_LIBRARY_PROVIDER: jj.JEJU_DREAM_LIBRARY_CANDIDATE_ID,
    }
    for provider, url in TARGETS:
        assert provider == stable_provider(url)
        assert candidates[provider] == candidate_id(normalized_duplicate_url(url))


def test_official_branch_registries_and_province_scope() -> None:
    assert jj.JEJU_CITY_MUNICIPALITY_CODE == "5011000000"
    assert jj.JEJU_INTEGRATED_BRANCHES == (
        (1, "설문대여성문화센터"),
        (45, "민속자연사박물관"),
        (46, "돌문화공원"),
        (47, "해녀박물관"),
        (48, "공공정책연수원"),
        (65, "제주문학관"),
        (67, "문화예술진흥원"),
        (70, "제주특별자치도"),
        (73, "자치경찰단"),
        (74, "제주어교육플랫폼"),
    )
    assert jj.JEJU_LIBRARY_BRANCHES == (
        (49, "한라도서관"),
        (50, "우당도서관"),
        (51, "탐라도서관"),
        (52, "제주시기적도서관"),
        (53, "애월도서관"),
        (54, "조천읍도서관"),
        (55, "한경도서관"),
    )
    assert len(jj.JEJU_RESIDENT_BRANCHES) == 26
    assert jj.JEJU_RESIDENT_BRANCHES[0] == (
        2, "일도1동", "주민자치센터(일도1동)"
    )
    assert jj.JEJU_RESIDENT_BRANCHES[-1] == (
        27, "우도면", "주민자치센터(우도면)"
    )
    assert len(jj.JEJU_YOUTH_BRANCHES) == 14
    assert [item[2] for item in jj.JEJU_AGRICULTURE_BRANCHES] == [
        "제주농업기술센터", "동부농업기술센터", "서부농업기술센터"
    ]
    for owner in ("province_integrated", "province_resident", "province_libraries"):
        assert "province" in jj.JEJU_OWNER_BOUNDARY_AUDIT[owner]["decision"]


def test_owner_boundaries_exclude_aliases_and_separate_owners() -> None:
    audit = jj.JEJU_OWNER_BOUNDARY_AUDIT
    assert "directory alias" in audit["city_reserve_landing"]["decision"]
    assert "aggregator" in audit["damoa"]["decision"]
    assert "not a course ledger" in audit["voucher"]["decision"]
    assert "education-office" in audit["education_office"]["decision"]
    assert "not the current application ledger" in audit["jeju_domin"]["decision"]
    assert "misassigned rows" in audit["province_resident"]["decision"]


class _NeverSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url: str, **_: Any) -> None:
        self.calls.append(url)
        raise AssertionError("network must not be reached")


@pytest.mark.parametrize(
    "url",
    (
        "https://www.jejusi.go.kr/qolup/checkUserApplCount.ajax",
        "https://www.jejusi.go.kr/youth/program/apply.do?mode=Insert&program_id=1",
        "https://www.jejusi.go.kr/star/intro/edcReqMemPop.ac?edc_num=EDC1",
        "https://www.jeju.go.kr/jumin/program/list.htm?act=join&course=1",
        "https://www.jeju.go.kr/booking/edu/edu.htm?act=download&file=1",
        "https://www.jeju.go.kr/common/fileDown.do?file=1",
        "https://www.jeju.go.kr/common/%66ileDown.do?file=1",
        "https://www.jeju.go.kr/%2564ownload?id=1",
        "https://www.jeju.go.kr/common/%25252564ownload?id=1",
        "https://www.jeju.go.kr/booking/edu/attachment.do?id=1",
        "https://www.jeju.go.kr/api/member/mypage.do",
        "https://www.jejusi.go.kr/qolup/info/lecture.do?cmd=apply&lecture_id=1",
        "https://jjdreamlib.or.kr/login.htm",
        "https://evil.example/apply",
    ),
)
def test_runner_refuses_application_applicant_and_host_escape_before_network(
    url: str,
) -> None:
    session = _NeverSession()
    runner = jj._Runner(lambda: session, 10, 10, lambda _: None)
    with pytest.raises(jj.JejuCityContractError):
        runner.get(url)
    assert session.calls == []


def _booking_payload(*, title: str = "도민 강좌", organ: int = 1) -> dict[str, Any]:
    return {
        "seq": 12001,
        "organ": organ,
        "title": title,
        "sep": "NORMAL",
        "target": "제주도민",
        "targetInput": None,
        "appStartDate": "2026-07-20",
        "appEndDate": "2026-07-30",
        "eduStart": "2026-08-01",
        "eduEnd": "2026-08-31",
        "eduTime": "10:00 ~ 12:00",
        "total": 20,
        "acceptCount": 3,
        "waitCount": 1,
        "location": "교육실",
        "pay": "무료",
        "joinStat": "JOIN",
        "use": True,
        "display": True,
        "accept": True,
        "organBean": {"seq": organ, "name": "설문대여성문화센터"},
    }


def test_booking_json_parser_locks_classification_and_minimizes_payload() -> None:
    row = jj._booking_row(
        _booking_payload(title="숲 체험 교실"),
        "integrated",
        1,
        "설문대여성문화센터",
    )
    assert row["provider_course_id"].endswith(":12001")
    assert row["branch"] == "설문대여성문화센터"
    assert row["domain_category"] == "체험"
    assert row["service_group"] == "체험"
    assert row["collection_category"] == "공공예약"
    assert row["source_group"] == "municipal_reservation"
    assert row["service_group_policy"] == "locked"
    assert row["classification_locked"] is True
    assert row["municipality_code"] == "5011000000"
    assert "organBean" not in row["raw_fields"]
    assert "tel" not in row and "phone" not in row


def test_booking_json_rejects_wrong_branch_and_non_public_rows() -> None:
    payload = _booking_payload()
    payload["organBean"] = {"seq": 1, "name": "다른 기관"}
    with pytest.raises(jj.JejuCityContractError, match="official branch changed"):
        jj._booking_row(payload, "integrated", 1, "설문대여성문화센터")
    payload = _booking_payload()
    payload["display"] = False
    with pytest.raises(jj.JejuCityContractError, match="non-public"):
        jj._booking_row(payload, "integrated", 1, "설문대여성문화센터")


def test_lifelong_parser_reads_exact_dates_and_never_emits_application_url() -> None:
    soup = BeautifulSoup(
        """
        <div class="db_list edu"><ul><li class="entry">
          <a href="/qolup/info/lecture.do?mode=detail&amp;lecture_id=1430&amp;currentPageNo=1">
            <div class="tag_edu"><span class="text">접수중</span></div>
            <em>[시민교육강좌]</em><strong>제주 시민 교실</strong>
            <ul class="list_sty01">
              <li>교육대상 : 제주시민</li><li>접수기간 : 2026-07-23 ~ 2026-07-31</li>
              <li>교육기간 : 2026-08-03 ~ 2026-12-14</li>
              <li>교육장소 : 평생학습관</li>
              <li>모집인원 : (정원)6/15명, (대기)0/3명</li>
            </ul>
          </a><a onclick="checkUserAppl('', '1430', '2026', '0')">신청</a>
        </li></ul></div>
        """,
        "lxml",
    )
    row = jj._parse_lifelong_page(soup)[0]
    assert row["provider_course_id"].endswith(":1430")
    assert row["start_date"] == "2026-08-03"
    assert row["end_date"] == "2026-12-14"
    assert row["capacity_total"] == 15
    assert row["capacity_current"] == 6
    assert "application_url" not in row
    assert row["raw_fields"]["source_application_control_present"] is True


def test_youth_parser_uses_official_branch_and_status_image() -> None:
    soup = BeautifulSoup(
        """
        <ul><li><a href="javaScript:doDetail('20260044');">
          <div class="label-typeB"><em>도남 청소년문화의집</em></div>
          <div class="label-typeA"><img alt="" src="/images/label-start.png"></div>
          <div class="title">니하오! 중국 탐험대</div><div class="memo">
            <dl><dt>참여대상</dt><dd>초등3~6학년</dd></dl>
            <dl><dt>모집일시</dt><dd>2026.07.21. 15시 ~ 2026.07.31. 18시</dd></dl>
            <dl><dt>접수방법</dt><dd>홈페이지 접수</dd></dl>
            <dl><dt>운영기간</dt><dd>2026-08-04 10:00 ~ 2026-08-18 10:00</dd></dl>
            <div class="text">중국어 및 중국문화 알기</div>
          </div>
        </a></li></ul>
        """,
        "lxml",
    )
    row = jj._parse_youth_page(soup)[0]
    assert row["branch"] == "도남 청소년문화의집"
    assert row["status"] == "OPEN"
    assert row["end_date"] == "2026-08-18"
    assert row["raw_url"].endswith("mode=Detail&program_id=20260044")


def test_resident_parser_normalizes_display_space_to_registry_name() -> None:
    soup = BeautifulSoup(
        """
        <table><tbody><tr><td>1</td><td>이도 1동</td><td>
        <a href="/jumin/program/list.htm?organPrefix=1001&amp;act=view&amp;course=5006">서예교실</a>
        </td><td>2026.08.01 ~ 2026.09.30</td><td></td><td>접수중</td></tr></tbody></table>
        """,
        "lxml",
    )
    row = jj._parse_resident_page(soup)[0]
    assert row["branch"] == "주민자치센터(이도1동)"
    assert row["raw_fields"]["organ_prefix"] == "1001"
    assert row["end_date"] == "2026-09-30"


def test_starlight_parser_marks_event_experience_and_discovers_no_applicant_url() -> None:
    soup = BeautifulSoup(
        """
        <ul><li class="eve"><a href="/star/intro/application.do?mode=detail&amp;edc_num=EDC0000191">
          <span class="label-title"><img alt="행사"></span>
          <span class="label-info"><img alt="모집중"></span>
          <h4 class="title">별빛 영화 이야기</h4><div class="info-area">
            <dl><dt>진행기간</dt><dd>2026.07.25 ~ 2026.07.25</dd></dl>
            <dl><dt>신청기간</dt><dd>2026.07.21 ~ 2026.07.23</dd></dl>
            <dl><dt>모집인원</dt><dd>30 명 / 팀</dd></dl>
            <dl><dt>모집대상</dt><dd>초등 5학년~성인</dd></dl>
            <dl><dt>진행 방식</dt><dd>오프라인 교육</dd></dl>
          </div></a><button onclick="window.open('/star/intro/edcReqMemPop.ac?edc_num=EDC0000191')">신청자목록</button>
        </li></ul>
        """,
        "lxml",
    )
    row = jj._parse_star_page(soup)[0]
    assert row["domain_category"] == "체험"
    assert row["service_group"] == "체험"
    assert row["raw_fields"]["applicant_list_control_present"] is True
    assert "applicant" not in row and "application_url" not in row


def test_agriculture_and_dream_library_parsers_use_exact_public_branches() -> None:
    agri = BeautifulSoup(
        """
        <ul><li><a href="/agri/farminginfo/education/jeju.htm?act=view&amp;seq=815">
          <h3 class="tit">농업기계 안전이용 교육</h3><span class="badge">접수대기</span>
          <div class="info-dl">
            <dl><dt>교육기간</dt><dd>26.09.15. ~ 26.10.08.</dd></dl>
            <dl><dt>신청기간</dt><dd>26.08.05. ~ 26.08.12.</dd></dl>
            <dl><dt>신청방법</dt><dd>전화및방문신청</dd></dl>
            <dl><dt>교육장소</dt><dd>제주농업기술센터</dd></dl>
            <dl><dt>신청자</dt><dd>0 명</dd></dl>
          </div></a></li></ul>
        """,
        "lxml",
    )
    row = jj._parse_agriculture_page(
        agri, "jeju", "제주농업기술센터", jj.JEJU_AGRICULTURE_BRANCHES[0][1]
    )[0]
    assert row["branch"] == "제주농업기술센터"
    assert row["provider_course_id"].endswith(":jeju:815")
    assert row["status"] == "SCHEDULED"

    dream = BeautifulSoup(
        """
        <table><tbody><tr><td>576</td><td><strong>제주어작가 강좌</strong></td>
        <td>26.07.10 ~ 26.07.20</td><td>26.07.26 ~ 26.07.26<br>14:00~16:00</td>
        <td>제주도민 누구나</td><td>25명<br>(20명)</td><td><span>접수중</span>
        <a href="/class/all.htm?act=view&amp;course=576">상세보기</a></td></tr></tbody></table>
        """,
        "lxml",
    )
    row = jj._parse_dream_library_page(dream)[0]
    assert row["branch"] == "제주꿈바당어린이도서관"
    assert row["capacity_total"] == 25
    assert row["capacity_current"] == 20


def test_empty_page_is_required_as_explicit_pagination_sentinel() -> None:
    page = BeautifulSoup(
        """
        <table><tbody><tr><td>1</td><td>봉개동</td><td>
        <a href="/jumin/program/list.htm?organPrefix=1001&amp;act=view&amp;course=1">서예</a>
        </td><td>2020.01.01 ~ 2020.02.01</td><td></td><td>접수종료</td></tr></tbody></table>
        """,
        "lxml",
    )
    empty = BeautifulSoup("<table><tbody></tbody></table>", "lxml")

    class Runner:
        calls: list[dict[str, Any]] = []

        def soup(self, _url: str, **kwargs: Any) -> BeautifulSoup:
            self.calls.append(kwargs["params"])
            return page if kwargs["params"]["page"] == 1 else empty

    runner = Runner()
    rows, meta = jj._crawl_html_pages(
        runner,
        "resident",
        jj.JEJU_RESIDENT_URL,
        "page",
        {},
        jj._parse_resident_page,
        3,
    )
    assert len(rows) == 1
    assert meta["sentinel_page"] == 2
    assert meta["sentinel_count"] == 0
    assert runner.calls == [{"page": 1}, {"page": 2}]


def test_current_detail_verification_checks_title_and_dates_without_posting() -> None:
    row = jj._parse_star_page(BeautifulSoup(
        """
        <li><a href="/star/intro/application.do?mode=detail&amp;edc_num=EDC0000191">
        <span class="label-title"><img alt="교육"></span><span class="label-info"><img alt="모집중"></span>
        <h4 class="title">별빛 교실</h4><div class="info-area">
        <dl><dt>진행기간</dt><dd>2026.07.25 ~ 2026.07.25</dd></dl>
        <dl><dt>신청기간</dt><dd>2026.07.20 ~ 2026.07.23</dd></dl>
        <dl><dt>모집인원</dt><dd>10명</dd></dl><dl><dt>모집대상</dt><dd>시민</dd></dl>
        <dl><dt>진행 방식</dt><dd>오프라인</dd></dl></div></a></li>
        """, "lxml"
    ))[0]

    class Runner:
        calls: list[str] = []

        def soup(self, url: str, **_: Any) -> BeautifulSoup:
            self.calls.append(url)
            return BeautifulSoup(
                '<div class="view-wrap"><strong>별빛 교실</strong>'
                '<p>운영기간 2026.07.25 ~ 2026.07.25</p></div>',
                "lxml",
            )

    runner = Runner()
    jj._verify_html_detail(runner, row, "star")
    assert runner.calls == [row["raw_url"]]
    assert row["raw_fields"]["detail_verified"] is True


def test_pii_sanitizer_removes_phone_email_and_resident_number() -> None:
    safe = jj._sanitize(
        "문의 064-123-4567 private@example.org 주민 900101-1234567 프로그램 안내"
    )
    assert "064-123-4567" not in safe
    assert "private@example.org" not in safe
    assert "900101-1234567" not in safe
    assert "프로그램 안내" in safe


def test_complete_collection_fails_closed_if_a_phone_number_reaches_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leaked = jj._common_row(
        jj.JEJU_INTEGRATED_PROVIDER,
        "phone-leak",
        "안전한 프로그램명",
        "설문대여성문화센터",
        jj.JEJU_INTEGRATED_URL,
        "integrated",
    )
    leaked["venue"] = "문의 064-123-4567"

    def fake_collect(*_args: Any, **_kwargs: Any):
        return [leaked], {
            "source_rows": 1,
            "source_current_count": 1,
            "current_count": 1,
            "pagination_complete": True,
            "details_complete": True,
        }

    monkeypatch.setattr(jj, "_collect_booking_api", fake_collect)
    rows, parser, meta = jj.collect(
        {
            "provider": jj.JEJU_INTEGRATED_PROVIDER,
            "url": jj.JEJU_INTEGRATED_URL,
        },
        today="2026-07-23",
        session_factory=_NeverSession,
    )
    assert parser == jj.JEJU_CITY_PARSER
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "PII-bearing output value" in meta["configured_collection_error"]


@pytest.mark.parametrize("provider,url", TARGETS)
def test_selected_live_owner_is_stable_twice_and_never_calls_application_endpoint(
    provider: str, url: str,
) -> None:
    if os.getenv("RUN_JEJU_CITY_LIVE") != "1":
        pytest.skip("set RUN_JEJU_CITY_LIVE=1 for the selected Jeju-si live audit")
    selected = os.getenv("JEJU_CITY_LIVE_OWNER", "all")
    owner = jj._OWNERS[provider]
    if selected not in {"all", owner, provider}:
        pytest.skip(f"live owner selection is {selected}")

    snapshots = []
    for _ in range(2):
        rows, parser, meta = jj.collect(
            {"provider": provider, "url": url},
            today="2026-07-23",
            timeout=30,
            max_requests=1_000,
            allow_raw_requests_for_tests=True,
            sleeper=lambda _: None,
        )
        assert parser == jj.JEJU_CITY_PARSER
        assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
        assert meta["pagination_complete"] is True
        assert meta["details_complete"] is True
        assert meta["application_endpoints_called"] == 0
        assert meta["applicant_endpoints_called"] == 0
        assert meta["detail_pages"] == meta["source_current_count"]
        assert all(row["municipality_code"] == "5011000000" for row in rows)
        assert all(
            (row["domain_category"], row["service_group"])
            in {("교육·강좌", "공공강좌"), ("체험", "체험")}
            for row in rows
        )
        snapshots.append((
            meta["source_total"], meta["source_current_count"],
            meta["source_identity_sha256"],
            sorted(row["provider_course_id"] for row in rows),
        ))
    assert snapshots[0] == snapshots[1]
