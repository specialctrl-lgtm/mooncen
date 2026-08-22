from __future__ import annotations

from dataclasses import dataclass
from html import escape
import os
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_taean as taean


@dataclass(frozen=True)
class Target:
    provider: str = taean.TAEAN_PROVIDER
    url: str = taean.TAEAN_CANONICAL_URL


class DummySession:
    def close(self) -> None:
        return None


class Response:
    def __init__(
        self,
        url: str,
        body: bytes | str,
        status_code: int = 200,
    ) -> None:
        self.url = url
        self.content = body if isinstance(body, bytes) else body.encode("utf-8")
        self.status_code = status_code


def _fixture_courses() -> list[dict[str, Any]]:
    return [
        {
            "identity": "4027",
            "title": (
                "태안군가족공감센터 2026년 여름방학 특강 "
                "「공학교실 8월 20일 - 고학년 - 수소전기차」수강생 모집"
            ),
            "category": "",
            "status": "접수마감",
            "method": "자체접수",
            "institution": "가족공감센터",
            "education_period": "2026-07-30 ~ 2026-03-30",
            "apply_period": "2026-07-06 09:00 ~ 2026-07-09 13:00",
            "schedule": "10:30 ~ 11:40",
            "capacity": "15명/45명 / 대기(1명)",
            "venue": "태안군가족공감센터 1층 다목적홀",
            "target": "어린이",
            "fee": "0",
            "content": "운영기간 : 2026. 8. 20.(목) 운영시간 : 10:30 ~ 11:40",
        },
        {
            "identity": "4037",
            "title": "파워포인트(10명)",
            "category": "컴퓨터",
            "status": "접수가능",
            "method": "자체접수",
            "institution": "전산관리팀",
            "education_period": "2026-07-27 ~ 2026-08-07",
            "apply_period": "2026-07-13 16:00 ~ 2026-07-23 16:00",
            "schedule": "오후 13:30 ~ 15:30",
            "capacity": "5명/10명",
            "venue": "태안군청 지하 전산교육장",
            "target": "일반",
            "fee": "0",
            "content": "파워포인트 슬라이드 작업",
        },
        {
            "identity": "4014",
            "title": "2026 두드림 청년정착지원 <청년 창업역량 실무교육>",
            "category": "기타",
            "status": "접수가능",
            "method": "기관접수",
            "institution": "태안청년창업비즈니스센터",
            "education_period": "2026-08-03 ~ 2026-08-07",
            "apply_period": "2026-07-06 09:00 ~ 2026-08-06 18:00",
            "schedule": "포스터 참고",
            "capacity": "0명/15명",
            "venue": "태안청년창업비즈니스센터",
            "target": "태안군 청년",
            "fee": "0",
            "content": "청년 창업역량 실무교육",
        },
        {
            "identity": "3969",
            "title": "2026년 하반기 농업기계 현장이용 기술교육 수강생 모집",
            "category": "기타",
            "status": "접수가능",
            "method": "방문접수",
            "institution": "농업기술센터",
            "education_period": "2026-08-03 ~ 2026-10-30",
            "apply_period": "2026-06-01 09:00 ~ 2026-10-12 18:00",
            "schedule": "14시~18시",
            "capacity": "0명/100명",
            "venue": "태안군농업기술센터 농업기계종합교육장",
            "target": "농업인",
            "fee": "0",
            "content": "농업기계 실습교육",
        },
        {
            "identity": "5000",
            "title": "장애인 평생학습 소양교육",
            "category": "소양",
            "status": "접수마감",
            "method": "기관접수",
            "institution": "평생학습관",
            "education_period": "2026-03-01 ~ 2026-12-01",
            "apply_period": "2026-02-01 09:00 ~ 2026-02-20 18:00",
            "schedule": "화 10:00 ~ 12:00",
            "capacity": "12명/20명",
            "venue": "장애인복지관 강당",
            "target": "태안군민",
            "fee": "15,000",
            "content": "공공 평생학습 교육",
        },
        {
            "identity": "4999",
            "title": "종료된 태안 교육",
            "category": "소양",
            "status": "접수마감",
            "method": "자체접수",
            "institution": "평생학습관",
            "education_period": "2026-05-01 ~ 2026-06-30",
            "apply_period": "2026-04-01 09:00 ~ 2026-04-20 18:00",
            "schedule": "수 10:00 ~ 12:00",
            "capacity": "10명/20명",
            "venue": "교육문화센터 204호",
            "target": "태안군민",
            "fee": "0",
            "content": "종료된 공공 교육",
        },
    ]


def _card(course: dict[str, Any]) -> str:
    category = (
        f"<span class='cate'>{escape(course['category'])}</span>"
        if course["category"]
        else ""
    )
    return f"""
      <div class="list">
        <div class="tit">{category}<a href="{taean.TAEAN_DETAIL_PATH}?eduNo={course['identity']}&amp;se=1">{escape(course['title'])}</a></div>
        <div class="state_btn"><b>{course['status']}</b><span>{course['method']}</span></div>
        <ul class="info">
          <li><b>교육기관</b>: {escape(course['institution'])}</li>
          <li><b>접수기간</b>: {escape(course['apply_period'])}</li>
          <li><b>신청/정원</b>: {escape(course['capacity'])}</li>
          <li><b>교육기간</b>: {escape(course['education_period'])}</li>
          <li><b>교육시간</b>: {escape(course['schedule'])}</li>
        </ul>
      </div>
    """


def _list_html(courses: list[dict[str, Any]], total: int) -> str:
    return f"""
      <html><body><div class="board_total">Total : {total:,}</div>
      <div class="courses_wrap">{''.join(_card(course) for course in courses)}</div>
      </body></html>
    """


def _detail_html(
    course: dict[str, Any],
    *,
    wrong_title: bool = False,
    wrong_application: bool = False,
    pii_target: bool = False,
    missing_correction_evidence: bool = False,
) -> str:
    title = "다른 태안 교육" if wrong_title else course["title"]
    target = "person@example.kr" if pii_target else course["target"]
    total = int(course["capacity"].split("/")[1].split("명")[0].replace(",", ""))
    if course["status"] == "접수가능" and course["method"] == "자체접수":
        control_identity = "9999" if wrong_application else course["identity"]
        control = (
            "<a class='writing' href='"
            f"{taean.TAEAN_APPLICATION_PATH}?pageIndex=1&amp;eduNo={control_identity}"
            "&amp;oneInwon=&amp;resvChk=N&amp;se=1'>수강신청</a>"
        )
    elif course["method"] == "기관접수":
        href = (
            "http://https://forms.gle/MtzAJpTG4Q5B9TND9"
            if course["status"] == "접수가능"
            else "http://태안군장애인복지관"
        )
        control = f"<a class='writing' href='{escape(href)}'>강좌신청</a>"
    elif course["status"] == "접수마감" and course["method"] == "자체접수":
        control = "<a class='writing' href='#'>접수마감</a>"
    else:
        control = ""
    content = "근거가 제거됨" if missing_correction_evidence else course["content"]
    fields = (
        ("강좌명", title),
        ("교육기간", course["education_period"]),
        ("교육시간", course["schedule"]),
        ("접수기간", course["apply_period"]),
        ("교육장소", course["venue"]),
        ("정원", str(total)),
        ("교육대상", target),
        ("강사명", "개인 강사명"),
        ("수강료", course["fee"]),
        ("담당자", "개인 담당자"),
        ("문의전화", "041-000-0000"),
        ("교육기관", course["institution"]),
        ("교육내용", content),
        ("기타사항", "신청자 개인정보와 첨부파일"),
    )
    rows = "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>"
        for label, value in fields
    )
    return f"""
      <html><body><main id="content"><div class="tablewrap"><table>{rows}</table></div>
      <div class="btn_box">{control}</div></main></body></html>
    """


class Source:
    def __init__(self, mode: str = "complete") -> None:
        self.mode = mode
        self.courses = _fixture_courses()
        self.calls: list[str] = []
        self.page_one_calls = 0
        self.lock = Lock()

    def __call__(self, _session: Any, url: str, _timeout: int) -> Response:
        with self.lock:
            self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == taean.TAEAN_APPLICATION_PATH:
            raise AssertionError("applicant endpoints must never be requested")
        if parsed.path == taean.TAEAN_LIST_PATH:
            page = int(query["pageIndex"][0])
            if page == 1 and self.mode == "redirect":
                return Response(url, "<not valid", status_code=302)
            with self.lock:
                if page == 1:
                    self.page_one_calls += 1
                page_one_call = self.page_one_calls
            values = [dict(course) for course in self.courses]
            if self.mode == "unknown_period":
                values[-1]["education_period"] = "~"
            if self.mode == "boundary_drift" and page == 1 and page_one_call > 1:
                values[0]["title"] += " 변경"
            start = (page - 1) * taean.TAEAN_PAGE_SIZE
            page_values = values[start : start + taean.TAEAN_PAGE_SIZE]
            if self.mode == "nonempty_sentinel" and page == 3:
                page_values = values[:1]
            return Response(url, _list_html(page_values, len(values)))
        if parsed.path == taean.TAEAN_DETAIL_PATH:
            identity = query["eduNo"][0]
            course = next(course for course in self.courses if course["identity"] == identity)
            return Response(
                url,
                _detail_html(
                    course,
                    wrong_title=self.mode == "detail_title" and identity == "5000",
                    wrong_application=self.mode == "wrong_application" and identity == "4037",
                    pii_target=self.mode == "pii_target" and identity == "3969",
                    missing_correction_evidence=(
                        self.mode == "correction_evidence" and identity == "4027"
                    ),
                ),
            )
        raise AssertionError(f"unexpected request: {url}")


@pytest.fixture(autouse=True)
def compact_page_size(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    if request.node.name == "test_live_taean_exact_2026_07_22_snapshot":
        return
    monkeypatch.setattr(taean, "TAEAN_PAGE_SIZE", 3)


def _collect(source: Source, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    options: dict[str, Any] = {
        "today": "2026-07-22",
        "timeout": 10,
        "max_pages": 10,
        "detail_limit": 20,
        "max_workers": 6,
        "session_factory": DummySession,
        "fetcher": source,
    }
    options.update(kwargs)
    rows, parser, meta = taean.collect(Target(), **options)
    assert parser == taean.TAEAN_PARSER
    return rows, meta


def test_canonical_owner_and_complete_source_topology() -> None:
    assert taean.TAEAN_PROVIDER == "MUNI_WWW_TAEAN_GO_KR_ADF2555A"
    assert taean.TAEAN_CANDIDATE_ID == "MUNI_IR_824C5741E529"
    assert taean.TAEAN_MUNICIPALITY_CODE == "4482500000"
    assert taean.is_target(Target())
    assert not taean.is_target(Target(url=taean.TAEAN_CANONICAL_URL + "/"))
    assert not taean.is_target(Target(url=taean.TAEAN_SOURCE_URL))
    assert not taean.is_target(Target(provider="MUNI_WRONG"))
    assert sum(
        view["audited_total_2026_07_22"]
        for view in taean.TAEAN_CATEGORY_VIEWS.values()
    ) == 2316
    assert len(taean.TAEAN_UNASSIGNED_LEGACY_IDS) == 27
    assert len(taean.TAEAN_AUDITED_HISTORICAL_PERIOD_DEFECT_IDS) == 46
    assert len(taean.TAEAN_AUDITED_BLANK_METHOD_IDS) == 33
    assert len(taean.TAEAN_AUDITED_MISSING_CAPACITY_IDS) == 17
    assert taean.TAEAN_DISCOVERY_AUDIT["generic_fanout"]["decision"].startswith(
        "replace"
    )


def test_complete_snapshot_covers_controls_repairs_branches_and_privacy() -> None:
    source = Source()
    rows, meta = _collect(source)

    assert meta["configured_collection_error"] == ""
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == meta["source_rows"] == 6
    assert meta["data_pages"] == 2
    assert meta["page_sizes"] == [3, 3]
    assert meta["empty_sentinel_page"] == 3
    assert meta["list_requests"] == 5
    assert meta["current_source_count"] == len(rows) == 5
    assert meta["detail_pages"] == 5
    assert meta["current_period_correction_count"] == 1
    assert meta["status_counts"] == {"CLOSED": 2, "OPEN": 3}
    assert meta["visible_application_control_count"] == 4
    assert meta["active_visible_application_control_count"] == 2
    assert meta["actionable_application_control_count"] == 1
    assert meta["external_controls_blocked"] == 2
    assert meta["active_external_controls_blocked"] == 1
    assert meta["insecure_external_controls_blocked"] == 2
    assert meta["open_offline_application_count"] == 1

    corrected = next(row for row in rows if row["raw_fields"]["identity"] == "4027")
    assert corrected["start_date"] == corrected["end_date"] == "2026-08-20"
    assert corrected["raw_fields"]["period_corrected"] is True
    internal = next(row for row in rows if row["raw_fields"]["identity"] == "4037")
    assert internal["reservation_available"] is True
    assert internal["application_url"] == taean.taean_application_url("4037")
    external = next(row for row in rows if row["raw_fields"]["identity"] == "4014")
    assert external["reservation_available"] is False
    assert external["application_url"] == ""
    assert external["raw_fields"]["external_control_blocked"] is True
    offline = next(row for row in rows if row["raw_fields"]["identity"] == "3969")
    assert offline["application_type"] == "OFFLINE_APPLY"
    partner = next(row for row in rows if row["raw_fields"]["identity"] == "5000")
    assert partner["branch"] == "태안군장애인복지관"
    assert {row["program_type"] for row in rows} == {"교육"}
    assert {row["municipality_full_name"] for row in rows} == {
        taean.TAEAN_MUNICIPALITY_NAME
    }
    payload = repr(rows)
    assert "041-000-0000" not in payload
    assert "개인 강사명" not in payload
    assert "개인 담당자" not in payload
    assert "운영기간 :" not in payload
    assert "신청자 개인정보" not in payload
    assert not any(taean.TAEAN_APPLICATION_PATH in url for url in source.calls)
    assert meta["forbidden_application_endpoint_requests"] == 0


@pytest.mark.parametrize(
    "mode",
    [
        "nonempty_sentinel",
        "boundary_drift",
        "unknown_period",
        "detail_title",
        "wrong_application",
        "pii_target",
        "correction_evidence",
    ],
)
def test_contract_and_privacy_drift_are_atomically_empty(mode: str) -> None:
    rows, meta = _collect(Source(mode))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"]


def test_redirect_is_rejected_before_body_parsing() -> None:
    source = Source("redirect")
    rows, meta = _collect(source)
    assert rows == []
    assert "HTTP 302" in meta["configured_collection_error"]
    assert source.calls == [taean.taean_list_url(1)]


def test_default_fetcher_explicitly_disables_redirects() -> None:
    class Session:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def get(self, _url: str, **kwargs: Any) -> Response:
            self.kwargs = kwargs
            return Response(taean.taean_list_url(1), "<html></html>")

    session = Session()
    taean._default_fetcher(session, taean.taean_list_url(1), 7)
    assert session.kwargs == {"timeout": 7, "allow_redirects": False}


def test_caps_and_dedupe_cardinality_fail_closed() -> None:
    rows, meta = _collect(Source(), max_pages=4)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "below required 5" in meta["configured_collection_error"]
    rows, meta = _collect(Source(), detail_limit=4)
    assert rows == []
    assert meta["source_cap_reached"] is True
    rows, meta = _collect(Source(), dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]


def test_noncanonical_target_never_fetches() -> None:
    source = Source()
    rows, parser, meta = taean.collect(
        Target(provider="MUNI_WRONG"),
        session_factory=DummySession,
        fetcher=source,
    )
    assert rows == []
    assert parser == taean.TAEAN_PARSER
    assert meta["configured_collection_error"]
    assert source.calls == []


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_MUNICIPAL_TESTS") != "1",
    reason="set RUN_LIVE_MUNICIPAL_TESTS=1 for the official live audit",
)
def test_live_taean_exact_2026_07_22_snapshot() -> None:
    rows, parser, meta = taean.collect(
        Target(),
        today="2026-07-22",
        timeout=40,
        max_pages=300,
        detail_limit=200,
        max_workers=12,
    )

    assert parser == taean.TAEAN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == meta["source_rows"] == 2343
    assert meta["data_pages"] == 24
    assert meta["page_sizes"] == [100] * 23 + [43]
    assert meta["empty_sentinel_page"] == 25
    assert meta["list_requests"] == 27
    assert meta["source_status_counts"] == {
        "접수가능": 6,
        "접수마감": 2336,
        "접수대기": 1,
    }
    assert meta["source_method_counts"] == {
        "자체접수": 2049,
        "기관접수": 98,
        "방문접수": 163,
        "미지정": 33,
    }
    assert meta["source_institution_counts"] == {
        "평생학습관": 835,
        "청소년수련관": 653,
        "가족센터": 358,
        "가족공감센터": 158,
        "전산관리팀": 105,
        "농업기술센터": 105,
        "태안청년창업비즈니스센터": 79,
        "유관 교육기관": 19,
        "먹거리유통과": 4,
        "미지정": 27,
    }
    assert meta["historical_period_defect_count"] == 46
    assert meta["source_period_correction_count"] == 9
    assert meta["noneducation_source_count"] == 1
    assert meta["current_source_count"] == len(rows) == 73
    assert meta["current_period_correction_count"] == 8
    assert meta["current_status_counts"] == {"접수가능": 5, "접수마감": 68}
    assert meta["current_method_counts"] == {
        "자체접수": 60,
        "기관접수": 11,
        "방문접수": 2,
    }
    assert meta["current_institution_counts"] == {
        "전산관리팀": 4,
        "가족공감센터": 36,
        "태안청년창업비즈니스센터": 2,
        "청소년수련관": 8,
        "평생학습관": 20,
        "농업기술센터": 2,
        "가족센터": 1,
    }
    assert meta["status_counts"] == {"CLOSED": 68, "OPEN": 5}
    assert meta["branch_counts"] == {
        "태안군가족공감센터": 36,
        "태안군청소년수련관": 8,
        "태안군청 전산교육장": 4,
        "태안군장애인가족지원센터": 3,
        "태안군 남면 평생학습센터": 3,
        "태안군 원북면 평생학습센터": 2,
        "태안군 고남면 평생학습센터": 2,
        "태안군 안면읍 평생학습센터": 2,
        "태안군농업기술센터": 2,
        "태안청년창업비즈니스센터": 2,
        "태안군 기원": 1,
        "(사)충남장애인부모회 태안지회": 1,
        "태안군장애인복지관": 1,
        "충남시각장애인협회 태안군지회": 1,
        "태안군가족센터": 1,
        "태안군 파크골프장": 1,
        "태안지역자활센터": 1,
        "태안군 소원면 평생학습센터": 1,
        "태안군교육문화센터": 1,
    }
    assert meta["visible_application_control_count"] == 71
    assert meta["active_visible_application_control_count"] == 4
    assert meta["actionable_application_control_count"] == 2
    assert meta["external_controls_blocked"] == 11
    assert meta["active_external_controls_blocked"] == 2
    assert meta["insecure_external_controls_blocked"] == 11
    assert meta["open_offline_application_count"] == 1
    assert "041-" not in repr(rows)
    assert "교육내용" not in repr(rows)
    assert meta["forbidden_application_endpoint_requests"] == 0
