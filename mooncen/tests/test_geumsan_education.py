from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
import os
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_geumsan as geumsan


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    list_title: str
    state: str
    accept: str
    method: str
    education_period: str
    apply_period: str
    operator: str
    venue: str
    category: str = "문화예술"
    education_time: str = "10:00~12:00"
    capacity: str = "2명/10명"
    target: str = "성인 > 성인일반"
    schedule: str = "매주 수요일"


class Response:
    def __init__(self, url: str, html: str, *, status_code: int = 200) -> None:
        self.url = url
        self.status_code = status_code
        self.headers: dict[str, str] = {"Content-Type": "text/html;charset=UTF-8"}
        self.content = html.encode("utf-8")


class DummySession:
    def close(self) -> None:
        return None


def _courses() -> list[Course]:
    expired = [
        Course(
            str(1000 + offset),
            f"지난 금산 교육 {offset}",
            f"지난 금산 교육 {offset}",
            "교육종료",
            "접수마감",
            "인터넷",
            "2026-01-01 ~ 2026-06-30",
            "2025-12-01 09:00 ~ 2025-12-15 18:00",
            "금산다락원",
            "금산다락원 여성의집",
        )
        for offset in range(10)
    ]
    return expired + [
        Course(
            "2001",
            "금산 미래 배움 교실",
            "금산 미래 배움...",
            "교육대기",
            "접수중",
            "인터넷",
            "2026-08-01 ~ 2026-08-31",
            "2026-07-01 09:00 ~ 2026-07-31 18:00",
            "자연치유센터",
            "자연치유센터",
            category="시민참여교육",
            capacity="4명/12명",
        ),
        Course(
            "2002",
            "상시 주민 교육",
            "상시 주민 교육",
            "교육중",
            "접수중",
            "방문",
            "상시",
            "상시접수",
            "금산읍 주민자치센터",
            "기타",
            target="교육대상선택 >",
            schedule="",
            capacity="0명/25명",
        ),
    ]


def _field(label: str, value: str) -> str:
    return f"<li><span>{escape(label)}<em> : </em></span><em>{escape(value)}</em></li>"


def _list_row(course: Course, *, identity: str | None = None) -> str:
    fields = [
        ("운영주체", course.operator),
        ("교육기간", course.education_period),
        ("교육시간", course.education_time),
        ("접수기간", course.apply_period),
        ("신청/정원", course.capacity),
        ("교육장소", course.venue),
        ("교육대상", course.target),
    ]
    if course.schedule:
        fields.append(("교육주기", course.schedule))
    rendered = "".join(_field(label, value) for label, value in fields)
    return f"""
      <a href="/site/lifelongedu/html/sub01/0102.html?mode=V&amp;mng_no={identity or course.identity}">
        <div class="col"><div class="inner">
          <div class="accept"><span>{escape(course.accept)}</span><em>{escape(course.method)}</em></div>
          <div class="in_top">
            <div class="cate"><span></span>{escape(course.category)}</div>
            <div class="tit">{escape(course.list_title)} <span class="cond">{escape(course.state)}</span></div>
          </div>
          <div class="list_con_w"><ul class="list_con">{rendered}</ul></div>
        </div></div>
      </a>
    """


def _pagination(observed: int, last: int) -> str:
    return f"""
      <ul class="pagination">
        <li class="page-item active"><a class="page-link" href="?&amp;GotoPage={observed}">{observed}</a></li>
        <li class="page-item"><a class="page-link" aria-label="last" href="?&amp;GotoPage={last}">last</a></li>
      </ul>
    """


def _list_html(courses: list[Course], *, observed: int, last: int = 2) -> str:
    return f"""
      <html><head><title>금산평생학습포털</title></head><body>
        <div class="program_con">{"".join(_list_row(course) for course in courses)}</div>
        {_pagination(observed, last)}
      </body></html>
    """


def _detail_html(
    course: Course,
    *,
    title: str | None = None,
    method: str | None = None,
    target: str | None = None,
    application_identity: str | None = None,
    include_application: bool | None = None,
) -> str:
    actual_method = method if method is not None else course.method
    fields = [
        ("운영주체", course.operator),
        ("교육기간", course.education_period),
        ("접수기간", course.apply_period),
        ("교육장소", course.venue),
    ]
    if course.schedule:
        fields.append(("교육주기", course.schedule))
    fields.extend(
        [
            (
                "교육대상",
                target if target is not None else course.target.removesuffix(" >").strip(),
            ),
            ("교육시간", course.education_time),
            ("신청/정원", course.capacity),
        ]
    )
    first = "".join(_field(label, value) for label, value in fields)
    contact = '<li><span>문의</span><a href="tel:041-000-0000"><em>041-000-0000</em></a></li>'
    method_field = _field("신청방법", actual_method)
    should_apply = (
        course.accept == "접수중" and course.method in {"인터넷", "혼합"}
        if include_application is None
        else include_application
    )
    application = ""
    if should_apply:
        bound = application_identity or course.identity
        application = f"""
          <div class="text-right mt_30">
            <a class="btn" href="/lifelongedu/html/sub01/0102.html?edu_mng_no={bound}&amp;mode=W">강좌신청</a>
          </div>
        """
    return f"""
      <html><head><title>금산평생학습포털</title></head><body>
        <div class="program_con program_view">
          <div class="accept"><span>{escape(course.accept)}</span><em>{escape(actual_method)}</em></div>
          <div class="in_top">
            <div class="cate"><span></span>{escape(course.category)}</div>
            <div class="tit">{escape(title or course.title)} <span class="cond">{escape(course.state)}</span></div>
          </div>
          <div class="list_con_w"><ul class="list_con">{first}{contact}{method_field}</ul></div>
        </div>
        {application}
      </body></html>
    """


class Fixture:
    def __init__(self) -> None:
        self.courses = _courses()
        self.calls: list[str] = []
        self.list_counts: Counter[int] = Counter()
        self.lock = Lock()
        self.mode = ""
        self.all_expired = False

    def __call__(self, _session, url: str, _timeout: int) -> Response:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        self.calls.append(url)
        if query.get("mode") == ["W"]:
            raise AssertionError("private applicant endpoint must never be requested")
        if query.get("mode") == ["V"]:
            identity = query["mng_no"][0]
            course = next(item for item in self.courses if item.identity == identity)
            if self.mode == "detail_http_failure" and identity == "2001":
                return Response(url, "temporary failure", status_code=503)
            if self.mode == "title_drift" and identity == "2001":
                return Response(url, _detail_html(course, title="다른 강좌"))
            if self.mode == "method_drift" and identity == "2001":
                return Response(url, _detail_html(course, method="방문"))
            if self.mode == "wrong_application_identity" and identity == "2001":
                return Response(
                    url,
                    _detail_html(course, application_identity="9999"),
                )
            if self.mode == "missing_application" and identity == "2001":
                return Response(
                    url,
                    _detail_html(course, include_application=False),
                )
            if self.mode == "unexpected_application" and identity == "2002":
                return Response(
                    url,
                    _detail_html(course, include_application=True),
                )
            if self.mode == "pii_target" and identity == "2001":
                return Response(
                    url,
                    _detail_html(course, target="person@example.kr"),
                )
            return Response(url, _detail_html(course))

        requested = int(query.get("GotoPage", ["1"])[0])
        with self.lock:
            self.list_counts[requested] += 1
            call = self.list_counts[requested]
        if requested == 1:
            rows = self.courses[:10]
            if self.mode == "first_boundary_drift" and call > 1:
                rows = [replace(rows[0], list_title="변경된 과거 강좌"), *rows[1:]]
            return Response(url, _list_html(rows, observed=1))

        rows = self.courses[10:]
        if self.all_expired:
            rows = [
                replace(
                    row,
                    state="교육종료",
                    accept="접수마감",
                    education_period="2026-01-01 ~ 2026-06-30",
                    apply_period="2025-12-01 09:00 ~ 2025-12-15 18:00",
                )
                for row in rows
            ]
        if self.mode == "duplicate_identity":
            rows = [replace(rows[0], identity="1000"), *rows[1:]]
        if self.mode == "expired_current_period":
            rows = [
                replace(rows[0], education_period="2026-01-01 ~ 2026-06-30"),
                *rows[1:],
            ]
        if requested == 2:
            return Response(url, _list_html(rows, observed=2))
        if requested == 3:
            observed = 3 if self.mode == "bad_clamp" else 2
            return Response(url, _list_html(rows, observed=observed))
        raise AssertionError(f"unexpected list page {requested}")


def _target(**changes: str) -> dict[str, str]:
    target = {
        "provider": geumsan.GEUMSAN_PROVIDER,
        "url": geumsan.GEUMSAN_CANONICAL_URL,
    }
    target.update(changes)
    return target


def _collect(fixture: Fixture, **kwargs):
    options = {
        "today": "2026-07-22",
        "timeout": 5,
        "max_pages": 5,
        "detail_limit": 10,
        "max_workers": 2,
        "session_factory": DummySession,
        "fetcher": fixture,
    }
    options.update(kwargs)
    return geumsan.collect(_target(), **options)


@pytest.mark.parametrize(
    "url",
    [
        geumsan.GEUMSAN_SITE_ALIAS_URL,
        geumsan.GEUMSAN_DARAGWON_SUBSET_URL,
        geumsan.GEUMSAN_REJECTED_JOB_URL,
        "http://www.geumsan.go.kr/lifelongedu/html/sub01/0102.html",
        "https://www.geumsan.go.kr.evil.example/lifelongedu/html/sub01/0102.html",
        "https://evil@www.geumsan.go.kr/lifelongedu/html/sub01/0102.html",
        "https://www.geumsan.go.kr:443/lifelongedu/html/sub01/0102.html",
        geumsan.GEUMSAN_CANONICAL_URL + "?GotoPage=1",
        geumsan.GEUMSAN_CANONICAL_URL + "#courses",
    ],
)
def test_exact_matcher_rejects_aliases_wrong_service_and_malicious_urls(url: str) -> None:
    assert not geumsan.is_target(_target(url=url))


def test_exact_matcher_and_audited_stable_identities() -> None:
    assert geumsan.is_target(_target())
    assert not geumsan.is_target(_target(provider="MUNI_WRONG"))
    assert geumsan.GEUMSAN_CANONICAL_CANDIDATE_ID == "MUNI_IR_9B585A6399AE"
    assert geumsan.GEUMSAN_REJECTED_JOB_CANDIDATE_ID == "MUNI_IR_920435073172"
    assert geumsan.GEUMSAN_REJECTED_JOB_PROVIDER == "MUNI_WWW_GEUMSAN_GO_KR_E9DFD479"
    assert "job-information" in geumsan.GEUMSAN_DISCOVERY_AUDIT["rejected_review_candidate"]["decision"]
    with pytest.raises(ValueError):
        geumsan.geumsan_list_url(0)
    with pytest.raises(ValueError):
        geumsan.geumsan_detail_url("../2001")
    with pytest.raises(ValueError):
        geumsan.geumsan_application_url("2001&mode=W")


def test_complete_snapshot_clamp_boundaries_details_controls_and_branches() -> None:
    fixture = Fixture()
    rows, parser, meta = _collect(fixture)

    assert parser == geumsan.GEUMSAN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_rows"] == meta["source_total"] == 12
    assert meta["declared_last_page"] == meta["data_pages"] == 2
    assert meta["post_last_clamp_page"] == 3
    assert meta["boundary_rechecks"] == 2
    assert meta["list_requests"] == meta["required_list_requests"] == 5
    assert meta["current_candidate_count"] == meta["current_source_count"] == 2
    assert meta["expired_count"] == 10
    assert meta["detail_attempts"] == meta["detail_pages"] == 2
    assert meta["application_control_count"] == 1
    assert meta["offline_open_count"] == 1
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["returned_count"] == 2
    assert meta["forbidden_applicant_endpoint_requests"] == 0
    assert fixture.list_counts == Counter({1: 2, 2: 2, 3: 1})

    by_id = {row["raw_fields"]["identity"]: row for row in rows}
    online = by_id["2001"]
    assert online["title"] == "금산 미래 배움 교실"
    assert online["status"] == "OPEN"
    assert online["reservation_available"] is True
    assert online["application_type"] == "ONLINE_RESERVATION_LOGIN_REQUIRED"
    assert parse_qs(urlparse(online["application_url"]).query) == {
        "edu_mng_no": ["2001"],
        "mode": ["W"],
    }
    assert online["branch"] == online["venue_name"] == "자연치유센터"
    assert online["capacity_current"] == 4
    assert online["capacity_total"] == 12
    assert online["start_date"] == "2026-08-01"
    assert online["end_date"] == "2026-08-31"

    offline = by_id["2002"]
    assert offline["status"] == "OPEN"
    assert offline["reservation_available"] is False
    assert offline["application_url"] == ""
    assert offline["application_type"] == "OFFLINE_VISIT"
    assert offline["branch"] == "금산읍 주민자치센터"
    assert offline["venue"] == "기타"
    assert offline["raw_fields"]["branch_basis"] == ("operator_fallback_for_generic_venue")
    assert offline["period"] == "상시"
    assert offline["start_date"] == offline["end_date"] == ""
    assert offline["schedule_raw"] == "10:00~12:00"

    for row in rows:
        assert row["municipality_code"] == "4471000000"
        assert row["municipality_full_name"] == "충청남도 금산군"
        assert row["domain_category"] == "교육·강좌"
        assert row["program_type"] == "교육"
        assert row["fee"] == "요금 별도 안내"
        assert row["raw_fields"]["source_fee_omitted"] is True
        assert "phone" not in row
        assert "contact" not in row
        assert "041-000-0000" not in repr(row)


def test_scheduled_acceptance_has_future_window_and_no_application_control() -> None:
    fixture = Fixture()
    fixture.courses[-2] = replace(
        fixture.courses[-2],
        accept="접수예정",
        apply_period="2026-07-23 09:00 ~ 2026-07-31 18:00",
    )

    rows, _, meta = _collect(fixture, today="2026-07-22")

    scheduled = next(row for row in rows if row["raw_fields"]["identity"] == "2001")
    assert meta["configured_collection_error"] == ""
    assert scheduled["status"] == "SCHEDULED"
    assert scheduled["reservation_available"] is False
    assert scheduled["application_url"] == ""
    assert scheduled["application_type"] == "INFO_ONLY"
    assert scheduled["apply_start"] == "2026-07-23"


def test_scheduled_acceptance_with_past_start_fails_closed() -> None:
    fixture = Fixture()
    fixture.courses[-2] = replace(
        fixture.courses[-2],
        accept="접수예정",
        apply_period="2026-07-01 09:00 ~ 2026-07-31 18:00",
    )

    rows, _, meta = _collect(fixture, today="2026-07-22")

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "scheduled source status/application dates disagree" in meta["configured_collection_error"]


def test_no_current_data_is_a_complete_empty_snapshot() -> None:
    fixture = Fixture()
    fixture.all_expired = True
    rows, _, meta = _collect(fixture)

    assert rows == []
    assert meta["source_rows"] == 12
    assert meta["current_source_count"] == 0
    assert meta["expired_count"] == 12
    assert meta["detail_attempts"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["no_current_data"] is True
    assert "교육종료" in meta["no_current_reason"]


def test_page_cap_fails_before_partial_traversal() -> None:
    fixture = Fixture()
    rows, _, meta = _collect(fixture, max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["list_requests"] == 1
    assert "max_pages" in meta["configured_collection_error"]


def test_detail_cap_fails_after_complete_list_without_partial_rows() -> None:
    fixture = Fixture()
    rows, _, meta = _collect(fixture, detail_limit=1)
    assert rows == []
    assert meta["source_rows"] == 12
    assert meta["pagination_complete"] is True
    assert meta["detail_pages"] == 0
    assert meta["source_cap_reached"] is True
    assert "detail_limit" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    "mode,error_fragment",
    [
        ("first_boundary_drift", "boundary stability recheck changed"),
        ("bad_clamp", "active page escaped declared last page"),
        ("duplicate_identity", "duplicate identities"),
        ("expired_current_period", "current state has expired period"),
        ("title_drift", "identity drift"),
        ("method_drift", "identity drift"),
        ("wrong_application_identity", "application control identity changed"),
        ("missing_application", "lacks application control"),
        ("unexpected_application", "unexpected application control"),
        ("pii_target", "contact-like data entered safe field"),
        ("detail_http_failure", "unexpected HTTP status 503"),
    ],
)
def test_contract_drift_and_detail_failure_are_fail_closed(mode: str, error_fragment: str) -> None:
    fixture = Fixture()
    fixture.mode = mode
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["full_snapshot_validated"] is False
    assert error_fragment in meta["configured_collection_error"]


def test_dedupe_may_not_change_official_identity_cardinality() -> None:
    fixture = Fixture()
    rows, _, meta = _collect(fixture, dedupe_rows=lambda values: values[:1])
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "dedupe changed official identity cardinality" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_CRAWLER_TESTS") != "1",
    reason="set RUN_LIVE_CRAWLER_TESTS=1 for the official Geumsan audit",
)
def test_live_complete_geumsan_catalogue() -> None:
    rows, parser, meta = geumsan.collect(
        _target(),
        today="2026-07-22",
        timeout=40,
        max_pages=100,
        detail_limit=500,
        max_workers=10,
    )
    assert parser == geumsan.GEUMSAN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_rows"] == 727
    assert meta["declared_last_page"] == 73
    assert meta["current_source_count"] == 204
    assert meta["expired_count"] == 523
    assert meta["application_control_count"] == 5
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert len(rows) == 204
    assert {row["municipality_code"] for row in rows} == {"4471000000"}
