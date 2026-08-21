from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
import os
import re
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_gumi as gumi


@dataclass(frozen=True)
class Course:
    identity: str
    number: int
    institution: str
    site_code: str
    category: str
    category_id: str
    title: str
    selection: str
    status: str
    education_period: str
    apply_period: str
    capacity_current: int
    capacity_total: int
    fee: str = "무료"
    cohort: str = "2026 - 1기"
    difficulty: str = "없음"
    venue: str = "구미시 교육실"
    target: str = "구미시민"
    visible_apply: bool = False
    write_action: bool = False


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
    expired: list[Course] = []
    for offset in range(10):
        expired.append(
            Course(
                identity=str(1000 + offset),
                number=14 - offset,
                institution="탄소제로교육관" if offset == 0 else "평생학습원",
                site_code="CB" if offset == 0 else "LL",
                category="주말 체험" if offset == 0 else "평생학습",
                category_id="701" if offset == 0 else "3040",
                title=("지난 가족체험 / 오전" if offset == 0 else f"지난 교육 {offset} / 오전"),
                selection="선착순",
                status="마감",
                education_period="2026-01-01(목) ~ 2026-06-30(화)",
                apply_period="2025-12-01 09:00 ~ 2025-12-15 18:00",
                capacity_current=10,
                capacity_total=10,
            )
        )
    return expired + [
        Course(
            "2001",
            4,
            "구미성리학역사관",
            "MU",
            "특별 교육프로그램",
            "3056",
            "선비와 구미의 길 / 오후",
            "선착순",
            "접수중",
            "2026-08-01(토) ~ 2026-10-17(토)",
            "2026-07-01 09:00 ~ 2026-07-31 18:00",
            2,
            10,
            venue="역사관 강당 (☎054-000-0000)",
            visible_apply=True,
            write_action=True,
        ),
        Course(
            "2002",
            3,
            "평생학습원",
            "LL",
            "평생학습",
            "3040",
            "미래 시민 / 야간 (초급)",
            "추첨",
            "접수 대기",
            "2026-09-01(화) ~ 2026-12-01(화)",
            "2026-08-01 09:00 ~ 2026-08-10 18:00",
            0,
            15,
            difficulty="초급",
        ),
        Course(
            "2003",
            2,
            "강동문화복지회관",
            "KW",
            "정기과정",
            "101",
            "생활 드로잉 / 오전",
            "선착순",
            "마감",
            "2026-07-01(수) ~ 2026-08-31(월)",
            "2026-06-01 09:00 ~ 2026-06-15 18:00",
            5,
            20,
            visible_apply=True,
            write_action=True,
        ),
        Course(
            "2004",
            1,
            "탄소제로교육관",
            "CB",
            "주말 체험",
            "701",
            "주말 가족체험 / 오후",
            "선착순",
            "접수중",
            "2026-08-03(월) ~ 2026-08-03(월)",
            "2026-07-01 10:00 ~ 2026-08-01 18:00",
            1,
            10,
            visible_apply=True,
            write_action=True,
        ),
    ]


def _site_options() -> str:
    options = ['<option value="">전체</option>']
    options.extend(f'<option value="{escape(code)}">{escape(name)}</option>' for code, name in gumi.GUMI_SITE_REGISTRY)
    return "".join(options)


def _category_options(*, drift: bool = False) -> str:
    first_identity = "999" if drift else "3040"
    return "".join(
        [
            '<option value="0">강좌분류</option>',
            f'<option value="{first_identity}">평생학습원 - 평생학습</option>',
            '<option value="3056">구미성리학역사관 - 특별 교육프로그램</option>',
            '<option value="101">강동문화복지회관 - 정기과정</option>',
            '<option value="701">탄소제로교육관 - 주말 체험</option>',
        ]
    )


def _list_row(course: Course, *, identity: str | None = None, title: str | None = None) -> str:
    actual_title = title if title is not None else course.title
    return f"""
      <tr>
        <td>{course.number}</td>
        <td>{escape(course.institution)}</td>
        <td>{escape(course.category)}</td>
        <td>{escape(course.cohort)}</td>
        <td class="p-subject"><a href="javascript:void(0)" onclick="goView({identity or course.identity}); return false;">
          <span class="lecture_recruit">{escape(course.selection)}</span>{escape(actual_title)}
        </a></td>
        <td>{escape(course.fee)}</td>
        <td><em>{course.capacity_current}</em> /{course.capacity_total}</td>
        <td>{escape(course.education_period)}</td>
        <td>{escape(course.apply_period)}</td>
        <td><span class="lecture_stat">{escape(course.status)}</span></td>
      </tr>
    """


def _list_html(
    courses: list[Course],
    *,
    observed: int,
    total: int = 14,
    last: int = 2,
    empty: bool = False,
    registry_drift: bool = False,
) -> str:
    headers = "".join(
        f"<th>{escape(value)}</th>"
        for value in (
            "번호",
            "기관",
            "강좌분류",
            "기수/과정",
            "강좌명/시간대",
            "수강료",
            "신청인원/ 정원",
            "교육기간",
            "접수기간",
            "상태",
        )
    )
    body = (
        '<tr><td colspan="10">신청가능한 강좌가 없습니다.</td></tr>'
        if empty
        else "".join(_list_row(course) for course in courses)
    )
    return f"""
      <html><head><title>강좌통합검색 - 교육·강좌 - 구미시통합예약</title></head><body>
        <select name="siteCode">{_site_options()}</select>
        <select name="cateIdx">{_category_options(drift=registry_drift)}</select>
        <div class="bbs_page">총 {total}건 [ {observed} /{last}페이지]</div>
        <table><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table>
      </body></html>
    """


def _detail_html(
    course: Course,
    *,
    title: str | None = None,
    action_identity: str | None = None,
    include_apply: bool | None = None,
    target: str | None = None,
    capacity_values: tuple[int, int, int] | None = None,
) -> str:
    detail_title = title or course.title
    if course.difficulty != "없음":
        detail_title = re.sub(rf"\s*\({re.escape(course.difficulty)}\)$", "", detail_title)
    apply = course.visible_apply if include_apply is None else include_apply
    bound = action_identity or course.identity
    write = ""
    if course.write_action or apply:
        write = f'frm.action = "/reservation/www/edu/app/write.do?prmIdx={bound}&key=208";'
    script = f"""
      <script>
        $('#siteCode').val('{course.site_code}');
        $('#cateIdx').val('{course.category_id}');
        {write}
        frm.action = "/reservation/www/edu/program/lottery/result.do?idx={course.identity}&key=208";
      </script>
    """
    apply_button = '<a class="btn type3" href="#" onclick="go_apply(); return false;">신청하기</a>' if apply else ""
    capacity_label = "신청/접수/대기" if capacity_values is not None else "신청/접수"
    capacity_text = (
        "/".join(f"{value}명" for value in capacity_values)
        if capacity_values is not None
        else f"{course.capacity_current}명/{course.capacity_total}명"
    )
    return f"""
      <html><head><title>강좌통합검색 - 교육·강좌 - 구미시통합예약</title></head><body>
        <table class="table type2">
          <tr><th>강좌명/시간대</th><td>{escape(course.selection + " " + detail_title)}</td>
              <th>교육 기간</th><td>{escape(course.education_period.replace("(토)", "").replace("(화)", "").replace("(수)", "").replace("(월)", ""))}</td></tr>
          <tr><th>접수 기간</th><td colspan="3">{escape(course.apply_period)}</td></tr>
          <tr><th>강의 시간</th><td>( 월 ) 10:00 ~ 12:00</td>
              <th>{capacity_label}</th><td>{capacity_text}</td></tr>
          <tr><th>소속</th><td>구미시</td><th>난이도</th><td>{escape(course.difficulty)}</td></tr>
          <tr><th>수강료</th><td>{escape(course.fee)}</td><th>교육장소</th><td>{escape(course.venue)}</td></tr>
          <tr><th>문의처</th><td>054-999-9999</td><th>교육 대상</th><td>{escape(target or course.target)}</td></tr>
          <tr><th>강의 계획서</th><td colspan="3">private-plan.pdf</td></tr>
          <tr><th>강의 소개</th><td colspan="3">자유 서술 상세</td></tr>
        </table>
        <div class="btn_wrap">{apply_button}<a href="#" onclick="go_list(); return false;">목록</a></div>
        {script}
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
        if parsed.path == gumi.GUMI_APPLICATION_PATH:
            raise AssertionError("private applicant endpoint must never be requested")
        if parsed.path == gumi.GUMI_DETAIL_PATH:
            identity = query["idx"][0]
            course = next(item for item in self.courses if item.identity == identity)
            if self.mode == "detail_http_failure" and identity == "2001":
                return Response(url, "temporary", status_code=503)
            if self.mode == "title_drift" and identity == "2001":
                return Response(url, _detail_html(course, title="다른 강좌 / 오후"))
            if self.mode == "wrong_action_identity" and identity == "2001":
                return Response(url, _detail_html(course, action_identity="9999"))
            if self.mode == "missing_open_control" and identity == "2001":
                return Response(url, _detail_html(course, include_apply=False))
            if self.mode == "pii_target" and identity == "2001":
                return Response(url, _detail_html(course, target="person@example.kr"))
            if self.mode == "wait_quota_smaller_than_applicant_excess" and identity == "2001":
                return Response(url, _detail_html(course, capacity_values=(10, 5, 2)))
            if self.mode == "wait_course_quota_drift" and identity == "2001":
                return Response(url, _detail_html(course, capacity_values=(10, 6, 2)))
            return Response(url, _detail_html(course))

        requested = int(query.get("page", ["1"])[0])
        with self.lock:
            self.list_counts[requested] += 1
            call = self.list_counts[requested]
        if requested == 1:
            rows = self.courses[:10]
            if self.mode == "first_boundary_drift" and call > 1:
                rows = [replace(rows[0], title="변경된 과거 강좌 / 오전"), *rows[1:]]
            return Response(
                url,
                _list_html(rows, observed=1, registry_drift=self.mode == "registry_drift" and call > 1),
            )
        rows = self.courses[10:]
        if self.mode in {"wait_quota_smaller_than_applicant_excess", "wait_course_quota_drift"}:
            rows = [replace(rows[0], capacity_total=5), *rows[1:]]
        if self.all_expired:
            rows = [
                replace(
                    row,
                    education_period="2026-01-01(목) ~ 2026-06-30(화)",
                    apply_period="2025-12-01 09:00 ~ 2025-12-15 18:00",
                    status="마감",
                    visible_apply=False,
                    write_action=False,
                )
                for row in rows
            ]
        if self.mode == "duplicate_identity":
            rows = [replace(rows[0], identity="1000"), *rows[1:]]
        if requested == 2:
            return Response(url, _list_html(rows, observed=2))
        if requested == 3:
            if self.mode == "nonempty_sentinel":
                return Response(url, _list_html(rows, observed=3))
            return Response(url, _list_html([], observed=3, empty=True))
        raise AssertionError(f"unexpected list page {requested}")


def _target(**changes: str) -> dict[str, str]:
    target = {"provider": gumi.GUMI_PROVIDER, "url": gumi.GUMI_CANONICAL_URL}
    target.update(changes)
    return target


def _collect(fixture: Fixture, **changes):
    options = {
        "today": "2026-07-22",
        "timeout": 5,
        "max_pages": 5,
        "detail_limit": 10,
        "max_workers": 2,
        "session_factory": DummySession,
        "fetcher": fixture,
    }
    options.update(changes)
    return gumi.collect(_target(), **options)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.gumi.go.kr/reservation/go.do?key=74",
        "https://www.gumi.go.kr/reservation/www/edu/program/list.do?key=260&siteCode=LL",
        "http://www.gumi.go.kr/reservation/www/edu/program/search.do?key=208",
        "https://www.gumi.go.kr.evil.example/reservation/www/edu/program/search.do?key=208",
        "https://evil@www.gumi.go.kr/reservation/www/edu/program/search.do?key=208",
        "https://www.gumi.go.kr:443/reservation/www/edu/program/search.do?key=208",
        gumi.GUMI_CANONICAL_URL + "&page=1",
        gumi.GUMI_CANONICAL_URL + "#courses",
    ],
)
def test_exact_matcher_rejects_aliases_and_malicious_urls(url: str) -> None:
    assert not gumi.is_target(_target(url=url))


def test_exact_matcher_and_audited_owner_identities() -> None:
    assert gumi.is_target(_target())
    assert not gumi.is_target(_target(provider="MUNI_WRONG"))
    assert gumi.GUMI_CANONICAL_CANDIDATE_ID == "MUNI_IR_8B0F767E88A9"
    assert gumi.GUMI_GO74_ALIAS_PROVIDER == "MUNI_WWW_GUMI_GO_KR_E8B61671"
    assert gumi.GUMI_GO74_ALIAS_CANDIDATE_ID == "MUNI_IR_0406AA593A15"
    assert len(gumi.GUMI_EXCLUDED_FILTER_ALIASES) == 14
    with pytest.raises(ValueError):
        gumi.gumi_list_url(0)
    with pytest.raises(ValueError):
        gumi.gumi_detail_url("../2001")
    with pytest.raises(ValueError):
        gumi.gumi_application_url("2001&key=1")


def test_complete_snapshot_routes_experience_and_preserves_exact_branches() -> None:
    fixture = Fixture()
    rows, parser, meta = _collect(fixture)

    assert parser == gumi.GUMI_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_rows"] == meta["source_total"] == 14
    assert meta["declared_last_page"] == meta["data_pages"] == 2
    assert meta["post_last_empty_page"] == 3
    assert meta["list_requests"] == meta["required_list_requests"] == 5
    assert meta["boundary_rechecks"] == 2
    assert meta["date_current_count"] == 4
    assert meta["expired_count"] == 10
    assert meta["experience_source_count"] == 2
    assert meta["current_experience_count"] == 1
    assert meta["experience_excluded_count"] == 0
    assert meta["current_experience_excluded_count"] == 0
    assert meta["current_candidate_count"] == meta["current_source_count"] == 4
    assert meta["detail_attempts"] == meta["detail_pages"] == 4
    assert meta["status_counts"] == {"OPEN": 2, "SCHEDULED": 1, "CLOSED": 1}
    assert meta["branch_counts"] == {
        "강동문화복지회관": 1,
        "구미성리학역사관": 1,
        "탄소제로교육관": 1,
        "평생학습원": 1,
    }
    assert meta["domain_category_counts"] == {"교육·강좌": 3, "체험·견학": 1}
    assert meta["service_group_counts"] == {"공공강좌": 3, "체험": 1}
    assert meta["application_control_count"] == 2
    assert meta["visible_application_control_count"] == 3
    assert meta["inactive_visible_application_control_count"] == 1
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert fixture.list_counts == Counter({1: 2, 2: 2, 3: 1})

    by_id = {row["raw_fields"]["identity"]: row for row in rows}
    opened = by_id["2001"]
    assert opened["branch"] == opened["venue_name"] == "구미성리학역사관"
    assert opened["branch_code"] == "GUMI_MU"
    assert opened["venue"] == "역사관 강당"
    assert opened["reservation_available"] is True
    assert opened["status"] == "OPEN"
    assert parse_qs(urlparse(opened["application_url"]).query) == {
        "prmIdx": ["2001"],
        "key": ["208"],
    }
    scheduled = by_id["2002"]
    assert scheduled["title"].endswith("(초급)")
    assert scheduled["status"] == "SCHEDULED"
    assert scheduled["application_url"] == ""
    stale = by_id["2003"]
    assert stale["status"] == "CLOSED"
    assert stale["reservation_available"] is False
    assert stale["application_url"] == ""
    assert stale["raw_fields"]["inactive_visible_application_control"] is True
    experience = by_id["2004"]
    assert experience["program_type"] == "체험"
    assert experience["domain_category"] == "체험·견학"
    assert experience["service_group"] == "체험"
    assert experience["raw_fields"]["experience_scope_verified"] is True
    for row in rows:
        assert row["municipality_code"] == "4719000000"
        assert row["municipality_full_name"] == "경상북도 구미시"
        assert row["description"] == row["title"]
        assert "054-999-9999" not in repr(row)
        assert "private-plan.pdf" not in repr(row)


def test_no_current_education_is_a_complete_empty_snapshot() -> None:
    fixture = Fixture()
    fixture.all_expired = True
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert meta["date_current_count"] == 0
    assert meta["current_source_count"] == 0
    assert meta["detail_attempts"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True


def test_wait_quota_does_not_claim_cumulative_applicant_excess_is_still_waiting() -> None:
    fixture = Fixture()
    fixture.mode = "wait_quota_smaller_than_applicant_excess"

    rows, _, meta = _collect(fixture)

    assert meta["snapshot_complete"] is True
    row = next(row for row in rows if row["raw_fields"]["identity"] == "2001")
    assert row["capacity_current"] == 2
    assert row["capacity_total"] == 5
    assert row["waitlist_current"] is None
    assert row["waitlist_total"] == 2


def test_caps_fail_before_partial_output() -> None:
    fixture = Fixture()
    rows, _, meta = _collect(fixture, max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["list_requests"] == 1
    assert "max_pages" in meta["configured_collection_error"]

    fixture = Fixture()
    rows, _, meta = _collect(fixture, detail_limit=2)
    assert rows == []
    assert meta["source_rows"] == 14
    assert meta["pagination_complete"] is True
    assert meta["detail_pages"] == 0
    assert "detail_limit" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    "mode,error_fragment",
    [
        ("first_boundary_drift", "boundary stability recheck changed"),
        ("registry_drift", "boundary stability recheck changed"),
        ("nonempty_sentinel", "post-last page is not structurally empty"),
        ("duplicate_identity", "duplicate identities"),
        ("title_drift", "title identity drift"),
        ("wrong_action_identity", "application action identity changed"),
        ("missing_open_control", "lacks visible application control"),
        ("pii_target", "contact-like data entered safe field"),
        ("wait_course_quota_drift", "list/detail wait-capacity drift"),
        ("detail_http_failure", "unexpected HTTP status 503"),
    ],
)
def test_contract_drift_and_detail_failures_are_fail_closed(mode: str, error_fragment: str) -> None:
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
    reason="set RUN_LIVE_CRAWLER_TESTS=1 for the official Gumi audit",
)
def test_live_complete_gumi_integrated_education_ledger() -> None:
    rows, parser, meta = gumi.collect(
        _target(),
        today="2026-07-22",
        timeout=40,
        max_pages=100,
        detail_limit=500,
        max_workers=10,
    )
    assert parser == gumi.GUMI_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_rows"] == 533
    assert meta["declared_last_page"] == 54
    assert meta["date_current_count"] == 166
    assert meta["experience_source_count"] == 24
    assert meta["current_experience_count"] == 6
    assert meta["experience_excluded_count"] == 0
    assert meta["current_experience_excluded_count"] == 0
    assert meta["current_source_count"] == 166
    assert meta["domain_category_counts"] == {"교육·강좌": 160, "체험·견학": 6}
    assert sum(meta["status_counts"].values()) == 166
    assert sum(meta["branch_counts"].values()) == 166
    assert meta["application_control_count"] >= 22
    assert meta["visible_application_control_count"] >= 31
    assert meta["inactive_visible_application_control_count"] == 9
    assert meta["snapshot_complete"] is True
    assert len(rows) == 166
