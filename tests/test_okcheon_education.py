from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from html import escape
import os
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_okcheon as okcheon


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    education_period: str
    status: str
    institution: str = "옥천군평생학습원"
    course_group: str = "정규과정"


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
            str(3100 + offset),
            f"지난 옥천교육 {offset}",
            "2026-01-05 ~ 2026-06-30",
            "교육종료",
        )
        for offset in range(10)
    ]
    return expired + [
        Course(
            "3200",
            "옥천 미래학습",
            "2026-08-01 ~ 2026-09-30",
            "온라인 수강신청",
            course_group="미래교육",
        )
    ]


def _list_row(course: Course) -> str:
    return f"""
      <tr>
        <td>{course.identity}</td>
        <td><span>{escape(course.institution)}</span><span>{escape(course.course_group)}</span></td>
        <td><a href="/edulife/viewTnCnteduProgrmU.do?progrmNo={course.identity}&amp;si2=2&amp;key=3890">{escape(course.title)}</a></td>
        <td>2026-07-01 09:00 ~ 2026-07-31 18:00</td>
        <td>{course.education_period}</td>
        <td>0 / 20</td>
        <td><span class="btn">{course.status}</span></td>
      </tr>
    """


def _list_html(courses: list[Course], *, observed_page: int) -> str:
    rows = "".join(_list_row(course) for course in courses)
    return f"""
      <html><head><title>옥천군 평생학습원</title></head><body>
        <div class="row"><span class="small">총 11건 [ {observed_page} / 2 페이지 ]</span></div>
        <table class="p-table"><tbody>{rows}</tbody></table>
      </body></html>
    """


def _detail_html(
    course: Course,
    *,
    title: str | None = None,
    apply_identity: str | None = None,
    target: str = "옥천군민",
) -> str:
    fields = [
        ("신청기간", "2026-07-01 09:00 ~ 2026-07-31 18:00"),
        ("교육기간", course.education_period),
        ("교육시간", "매주 토요일 10:00~12:00"),
        ("교육장소", "옥천군평생학습원 1강의실"),
        ("교육대상", target),
        ("수강료", "무료"),
        ("신청/정원", "전체 : 3 / 20"),
    ]
    detail_fields = "".join(
        f'<div class="desc_item"><span class="desc_title">{escape(label)}</span>'
        f'<span class="desc_text">{escape(value)}</span></div>'
        for label, value in fields
    )
    application = f"""
      <a href="/edulife/addTnCnteduProgrmApplcntViewU.do?progrmNo={apply_identity or course.identity}&amp;si2=2&amp;key=3890">
        신청하기
      </a>
    """
    return f"""
      <html><head><title>옥천군 평생학습원</title></head><body>
        <div class="edu_program_item">
          <h3 class="category_title">{escape(title or course.title)}</h3>
          <div class="category_bdg"><span class="category">{course.status}</span></div>
          {detail_fields}
        </div>
        {application}
      </body></html>
    """


class Fixture:
    def __init__(self) -> None:
        self.courses = _courses()
        self.calls: list[str] = []
        self.counts: Counter[int] = Counter()
        self.lock = Lock()
        self.first_page_drift = False
        self.bad_clamp = False
        self.detail_mode = ""

    def __call__(self, _session, url: str, _timeout: int) -> Response:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        self.calls.append(url)
        if parsed.path == okcheon.OKCHEON_DETAIL_PATH:
            identity = query["progrmNo"][0]
            course = next(value for value in self.courses if value.identity == identity)
            if self.detail_mode == "http_failure":
                return Response(url, "upstream failure", status_code=503)
            if self.detail_mode == "title_drift":
                return Response(url, _detail_html(course, title="다른 강좌"))
            if self.detail_mode == "wrong_apply_identity":
                return Response(url, _detail_html(course, apply_identity="9999"))
            if self.detail_mode == "pii_target":
                return Response(
                    url,
                    _detail_html(course, target="담당자 example.person@oc.go.kr"),
                )
            return Response(url, _detail_html(course))

        assert parsed.path == okcheon.OKCHEON_LIST_PATH
        requested = int(query.get("cpn", ["1"])[0])
        with self.lock:
            self.counts[requested] += 1
            call_number = self.counts[requested]
        if requested == 1:
            page_rows = self.courses[:10]
            if self.first_page_drift and call_number > 1:
                page_rows = [
                    Course(
                        page_rows[0].identity,
                        page_rows[0].title + " 변경",
                        page_rows[0].education_period,
                        page_rows[0].status,
                    ),
                    *page_rows[1:],
                ]
            return Response(url, _list_html(page_rows, observed_page=1))
        if requested == 2:
            return Response(url, _list_html(self.courses[10:], observed_page=2))
        if requested == 3:
            observed = 3 if self.bad_clamp else 2
            return Response(url, _list_html(self.courses[10:], observed_page=observed))
        raise AssertionError(f"unexpected list page request: {requested}")


def _target(**changes: str) -> dict[str, str]:
    target = {
        "provider": okcheon.OKCHEON_PROVIDER,
        "url": okcheon.OKCHEON_CANONICAL_URL,
    }
    target.update(changes)
    return target


def _collect(fixture: Fixture, **kwargs):
    return okcheon.collect(
        _target(),
        today="2026-07-22",
        timeout=5,
        max_pages=5,
        detail_limit=2,
        max_workers=2,
        session_factory=DummySession,
        fetcher=fixture,
        **kwargs,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://www.oc.go.kr/edulife/selectTnCnteduProgrmListU.do?key=3890&si2=2",
        "https://www.oc.go.kr.evil.example/edulife/selectTnCnteduProgrmListU.do?key=3890&si2=2",
        "https://www.oc.go.kr@evil.example/edulife/selectTnCnteduProgrmListU.do?key=3890&si2=2",
        "https://evil@www.oc.go.kr/edulife/selectTnCnteduProgrmListU.do?key=3890&si2=2",
        "https://www.oc.go.kr:443/edulife/selectTnCnteduProgrmListU.do?key=3890&si2=2",
        okcheon.OKCHEON_CANONICAL_URL + "&cpn=1",
        okcheon.OKCHEON_CANONICAL_URL + "&si2=2",
        okcheon.OKCHEON_CANONICAL_URL + "#programmes",
    ],
)
def test_exact_matcher_rejects_noncanonical_and_malicious_urls(url: str) -> None:
    assert not okcheon.is_target(_target(url=url))


def test_exact_matcher_accepts_only_canonical_owner() -> None:
    assert okcheon.is_target(_target())
    assert not okcheon.is_target(_target(provider="MUNI_WRONG"))
    assert not okcheon.is_target(_target(url=okcheon.OKCHEON_CANONICAL_URL + "&ignored="))
    with pytest.raises(ValueError):
        okcheon.okcheon_detail_url("../3200")
    with pytest.raises(ValueError):
        okcheon.okcheon_list_url(0)


def test_mixed_ledger_classification_uses_only_explicit_experience_semantics() -> None:
    assert (
        okcheon._service_family("2488", "신나는 드론 체험 클래스", "청소년수련관")
        == "experience"
    )
    assert (
        okcheon._service_family("2477", "컬러테라피(감정향수)", "화목한 원데이 클래스")
        == "experience"
    )
    assert (
        okcheon._service_family(
            "2491",
            "청소년수련관 (여름방학) 원-데이 눈이 번쩍 AI 클래스",
            "청소년수련관",
        )
        == "experience"
    )
    assert okcheon._service_family("3200", "옥천 미래학습", "미래교육") == "education"


def test_complete_multipage_snapshot_clamp_boundaries_detail_identity_and_branch() -> None:
    fixture = Fixture()
    rows, parser, meta = _collect(fixture)

    assert parser == okcheon.OKCHEON_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_rows"] == meta["source_total"] == 11
    assert meta["data_pages"] == 2
    assert meta["post_last_clamp_page"] == 3
    assert meta["boundary_rechecks"] == 2
    assert meta["list_requests"] == 5
    assert meta["current_source_count"] == 1
    assert meta["expired_count"] == 10
    assert meta["detail_pages"] == 1
    assert meta["returned_count"] == 1
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["application_control_count"] == 1
    assert fixture.counts == Counter({1: 2, 2: 2, 3: 1})

    assert len(rows) == 1
    row = rows[0]
    assert row["provider_course_id"] == f"{okcheon.OKCHEON_PROVIDER}:3200"
    assert row["status"] == "OPEN"
    assert row["reservation_available"] is True
    assert parse_qs(urlparse(row["application_url"]).query)["progrmNo"] == ["3200"]
    assert row["branch"] == row["venue"] == row["venue_name"] == "옥천군평생학습원 1강의실"
    assert row["branch_code"].startswith("OKCHEON_")
    assert row["capacity_current"] == 3
    assert row["capacity_total"] == 20
    assert row["raw_fields"]["detail_verified"] is True
    assert row["raw_fields"]["application_control_present"] is True
    assert row["service_family"] == "education"
    assert meta["education_rows"] == 1
    assert meta["experience_rows"] == 0
    assert meta["classification_complete"] is True


@pytest.mark.parametrize(
    ("max_pages", "detail_limit", "expected"),
    [
        (4, 2, "max_pages 4 below required 5"),
        (5, 0, "detail_limit 0 below required 1"),
    ],
)
def test_request_and_detail_caps_fail_closed_without_partial_rows(
    max_pages: int,
    detail_limit: int,
    expected: str,
) -> None:
    fixture = Fixture()
    rows, _parser, meta = okcheon.collect(
        _target(),
        today="2026-07-22",
        timeout=5,
        max_pages=max_pages,
        detail_limit=detail_limit,
        max_workers=2,
        session_factory=DummySession,
        fetcher=fixture,
    )

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert expected in meta["configured_collection_error"]
    assert not any(urlparse(url).path == okcheon.OKCHEON_DETAIL_PATH for url in fixture.calls)


def test_first_boundary_drift_fails_closed_before_details() -> None:
    fixture = Fixture()
    fixture.first_page_drift = True
    rows, _parser, meta = _collect(fixture)

    assert rows == []
    assert meta["pagination_complete"] is False
    assert meta["snapshot_complete"] is False
    assert "first page recheck failed" in meta["configured_collection_error"]
    assert not any(urlparse(url).path == okcheon.OKCHEON_DETAIL_PATH for url in fixture.calls)


def test_post_last_page_must_clamp_to_exact_last_page() -> None:
    fixture = Fixture()
    fixture.bad_clamp = True
    rows, _parser, meta = _collect(fixture)

    assert rows == []
    assert meta["pagination_complete"] is False
    assert "exact last-page clamp missing" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("http_failure", "unexpected HTTP status 503"),
        ("title_drift", "detail identity drift"),
        ("wrong_apply_identity", "online application control missing"),
        ("pii_target", "contact data persisted"),
    ],
)
def test_detail_application_identity_and_pii_fail_closed(mode: str, expected: str) -> None:
    fixture = Fixture()
    fixture.detail_mode = mode
    rows, _parser, meta = _collect(fixture)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert expected in meta["configured_collection_error"]


def test_dedupe_cannot_reduce_complete_current_identity_set() -> None:
    fixture = Fixture()
    rows, _parser, meta = _collect(fixture, dedupe_rows=lambda _rows: [])
    assert rows == []
    assert "dedupe changed the complete identity set" in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_wrong_target_and_invalid_limits_return_no_rows() -> None:
    fixture = Fixture()
    rows, _parser, meta = okcheon.collect(
        _target(provider="WRONG"),
        session_factory=DummySession,
        fetcher=fixture,
    )
    assert rows == []
    assert "canonical Okcheon owner" in meta["configured_collection_error"]

    rows, _parser, meta = okcheon.collect(
        _target(),
        max_workers=0,
        session_factory=DummySession,
        fetcher=fixture,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "invalid collection limits" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("OKCHEON_EDUCATION_LIVE") != "1",
    reason="set OKCHEON_EDUCATION_LIVE=1 for official-source verification",
)
def test_live_okcheon_complete_snapshot() -> None:
    rows, parser, meta = okcheon.collect(
        _target(),
        today=date(2026, 7, 22),
        timeout=40,
        max_pages=200,
        detail_limit=300,
        max_workers=2,
    )

    assert parser == okcheon.OKCHEON_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_rows"] == meta["source_total"]
    assert meta["list_requests"] == meta["data_pages"] + 3
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
    assert len(rows) == meta["returned_count"] == meta["current_source_count"]
    assert all(row["municipality_code"] == okcheon.OKCHEON_MUNICIPALITY_CODE for row in rows)
    assert all(not okcheon._privacy(row) for row in rows)
