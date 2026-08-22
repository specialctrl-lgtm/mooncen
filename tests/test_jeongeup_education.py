from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from hashlib import sha1, sha256
import os
from threading import Lock
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import pytest
import requests

from Crawler import municipal_jeongeup as jeongeup


TARGET = {
    "provider": jeongeup.JEONGEUP_PROVIDER,
    "url": jeongeup.JEONGEUP_CANONICAL_URL,
}


@dataclass(frozen=True)
class SyntheticCourse:
    identity: str
    branch_menu: str
    title: str
    raw_status: str
    status_class: str
    apply_start: str
    apply_end: str
    event_start: str
    event_end: str
    venue: str
    schedule: str
    target: str
    fee: str
    capacity_current: int
    capacity_total: int


def _branch_courses() -> dict[str, list[SyntheticCourse]]:
    counts = (13, 4, 21, 2)
    result: dict[str, list[SyntheticCourse]] = {}
    identity_number = 9_000_040
    for branch_index, (branch, count) in enumerate(
        zip(jeongeup.JEONGEUP_BRANCHES, counts)
    ):
        courses: list[SyntheticCourse] = []
        for index in range(count):
            if branch_index == 0 and index == 0:
                raw_status, status_class = "접수중", "rec01"
                apply_start, apply_end = "2026-07-20", "2026-07-31"
                event_start = event_end = "2026-08-10"
            elif branch_index == 0 and index == 1:
                raw_status, status_class = "교육중", "rec04"
                apply_start, apply_end = "2026-06-01", "2026-06-20"
                event_start, event_end = "2026-07-01", "2026-08-05"
            elif branch_index == 1 and index == 0:
                raw_status, status_class = "계획중", "rec01"
                apply_start, apply_end = "2026-07-24", "2026-08-01"
                event_start = event_end = "2026-08-20"
            elif branch_index == 2 and index == 0:
                raw_status, status_class = "교육중", "rec04"
                apply_start, apply_end = "2026-06-01", "2026-06-15"
                event_start, event_end = "2026-07-10", "2026-07-30"
            elif branch_index == 3 and index == 0:
                raw_status, status_class = "접수완료", "rec03"
                apply_start, apply_end = "2026-07-01", "2026-07-15"
                event_start = event_end = "2026-07-28"
            else:
                raw_status, status_class = "교육종료", "rec04"
                apply_start, apply_end = "2025-01-01", "2025-01-15"
                event_start = event_end = "2025-02-01"
            courses.append(
                SyntheticCourse(
                    identity=f"RE{identity_number:07d}",
                    branch_menu=branch.list_menu,
                    title=f"{branch.name} 합성 강좌 {index + 1:02d}",
                    raw_status=raw_status,
                    status_class=status_class,
                    apply_start=apply_start,
                    apply_end=apply_end,
                    event_start=event_start,
                    event_end=event_end,
                    venue=f"{branch.name} 강의실 {index % 3 + 1}",
                    schedule="토 10:00~12:00",
                    target="정읍시민",
                    fee="무료/무료" if index % 2 else "3만원",
                    capacity_current=min(index + 1, 12),
                    capacity_total=10,
                )
            )
            identity_number -= 1
        result[branch.list_menu] = courses
    return result


def _options(values: tuple[tuple[str, str], ...]) -> str:
    return "".join(
        f'<option value="{value}">{name}</option>' for value, name in values
    )


def _full_detail_href(
    branch: jeongeup.JeongeupBranch,
    course: SyntheticCourse,
    requested_page: int,
) -> str:
    return jeongeup.JEONGEUP_LINK_PATH + "?" + urlencode(
        (
            ("menuCd", branch.detail_menu),
            ("reUniqId", course.identity),
            ("searchCondition", "RE_NAME"),
            ("searchKeyword", ""),
            ("orderField", ""),
            ("orderSort", "asc"),
            ("searchDateGubun", "3"),
            ("startPage", str(requested_page)),
        )
    )


def _pager_href(branch: jeongeup.JeongeupBranch, page: int) -> str:
    return jeongeup.JEONGEUP_LINK_PATH + "?" + urlencode(
        (
            ("menuCd", branch.list_menu),
            ("searchCondition", "RE_NAME"),
            ("searchKeyword", ""),
            ("orderField", ""),
            ("orderSort", "asc"),
            ("searchDateGubun", "3"),
            ("startPage", str(page)),
        )
    )


def _row_html(
    branch: jeongeup.JeongeupBranch,
    course: SyntheticCourse,
    requested_page: int,
    *,
    title_suffix: str = "",
) -> str:
    href = _full_detail_href(branch, course, requested_page)
    if course.raw_status == "접수중":
        control = (
            f'<a class="possible possible01 blink" href="{href}">'
            "<span>신청하기</span></a>"
        )
    else:
        control_text = (
            "접수대기"
            if jeongeup.JEONGEUP_STATUS_MAP[course.raw_status] == "SCHEDULED"
            else "접수마감"
        )
        control = (
            f'<a class="possible possible02"><span>{control_text}</span></a>'
        )
    return f"""
      <li>
        <dl>
          <dt><a href="{href}">{course.title}{title_suffix}</a></dt>
          <dd><strong>교육기간</strong> {course.event_start} ~ {course.event_end}</dd>
          <dd><strong>접수기간</strong> {course.apply_start} ~ {course.apply_end}</dd>
          <dd><strong>교육장</strong>{course.venue}</dd>
        </dl>
        <p class="rec {course.status_class}">{course.raw_status}
          <span>{course.capacity_current}/{course.capacity_total}</span>
        </p>
        {control}
      </li>
    """


def _list_html(
    branch: jeongeup.JeongeupBranch,
    courses: list[SyntheticCourse],
    requested_page: int,
    *,
    bad_overflow: bool = False,
    title_suffix: str = "",
) -> str:
    last = max(1, (len(courses) + jeongeup.JEONGEUP_PAGE_SIZE - 1) // jeongeup.JEONGEUP_PAGE_SIZE)
    if requested_page > last:
        actual = 1 if bad_overflow else last
        current_marker = ""
    else:
        actual = requested_page
        current_marker = f'<span class="on">{actual}</span>'
    start = (actual - 1) * jeongeup.JEONGEUP_PAGE_SIZE
    page_rows = courses[start : start + jeongeup.JEONGEUP_PAGE_SIZE]
    body = "".join(
        _row_html(
            branch,
            course,
            requested_page,
            title_suffix=title_suffix if actual == 1 and index == 0 else "",
        )
        for index, course in enumerate(page_rows)
    )
    links = "".join(
        f'<span><a href="{_pager_href(branch, page)}">{page}</a></span>'
        for page in range(1, last + 1)
    )
    open_count = sum(course.raw_status == "접수중" for course in courses)
    closed_count = sum(
        course.event_end >= "2026-07-23" and course.raw_status != "접수중"
        for course in courses
    )
    return f"""
    <html><head><meta charset="utf-8"><title>교육/강좌 &gt; {branch.name}</title></head>
    <body><div id="content"><h3>{branch.name}</h3>
      <form name="listForm" method="get" action="{jeongeup.JEONGEUP_LINK_PATH}">
        <input type="hidden" name="menuCd" value="{branch.list_menu}">
        <input type="hidden" name="startPage" value="{requested_page}">
        <input type="hidden" name="searchCondition" value="RE_NAME">
        <input type="hidden" name="orderField" value="">
        <input type="hidden" name="searchDateGubun" value="3">
        <select id="lectureType" name="lectureType">{_options(branch.category_options)}</select>
        <ul class="btn_condition">
          <li><button id="all" onclick="searchDatefunc('3')">전체</button></li>
          <li><button onclick="searchDatefunc('1')">접수중</button></li>
        </ul>
        <input type="text" name="searchKeyword" value="">
      </form>
      <ul class="search_result">
        <li>모집중 : <span>{open_count}</span>건</li>
        <li>마감 : {closed_count}건</li>
        <li class="last">검색된 결과 : <span>{len(courses)}</span>건</li>
      </ul>
      <div class="bbs_list01"><ul>{body}</ul></div>
      <div class="bbs_page">{current_marker}{links}</div>
    </div></body></html>
    """


def _detail_html(
    branch: jeongeup.JeongeupBranch,
    course: SyntheticCourse,
    page: int,
    *,
    title_suffix: str = "",
    omit_application: bool = False,
) -> str:
    if course.raw_status == "접수중" and not omit_application:
        apply_control = '<button type="button" onclick="writeFunc();">신청하기</button>'
    else:
        control_text = (
            "접수대기"
            if jeongeup.JEONGEUP_STATUS_MAP[course.raw_status] == "SCHEDULED"
            else "접수마감"
        )
        apply_control = f"<button>{control_text}</button>"
    back = _pager_href(branch, page)
    return f"""
    <html><head><meta charset="utf-8">
      <title>교육/강좌 &gt; {branch.name} &gt; 신청하기</title></head>
    <body><div class="edu_view01">
      <div class="info"><h4>{course.title}{title_suffix}</h4>
        <table class="view_table"><tbody>
          <tr><th>접수기간</th><td>{course.apply_start} ~ {course.apply_end}</td></tr>
          <tr><th>교육기간</th><td>{course.event_start} ~ {course.event_end}</td></tr>
          <tr><th>교육시간</th><td>{course.schedule}</td></tr>
          <tr><th>교육장</th><td>{course.venue}</td></tr>
          <tr><th>강사명</th><td>저장하면 안 되는 개인강사</td></tr>
          <tr><th>수강료/재료비</th><td>{course.fee}</td></tr>
          <tr><th>교육대상</th><td>{course.target}</td></tr>
          <tr><th>신청/정원</th><td>{course.capacity_current} / {course.capacity_total}</td></tr>
          <tr><th>문의담당자</th><td>저장하면 안 되는 담당자</td></tr>
          <tr><th>문의전화</th><td>063-539-1234</td></tr>
          <tr><th>교육내용</th><td>저장하면 안 되는 자유 서술 본문</td></tr>
          <tr><th>강의자료</th><td><a href="/private/material.hwp">개인자료.hwp</a></td></tr>
          <tr><th>접수상태</th><td>{course.raw_status}</td></tr>
        </tbody></table>
      </div>
      <div class="btn">
        <p class="btn_apply">{apply_control}</p>
        <p class="btn_back"><a href="{back}">← 목록페이지로 이동</a></p>
      </div>
      <div id="writeArea" style="display:none">
        <form action="/reserve/applicationSubmit" method="post">
          <input name="applicantName"><input name="applicantPhone">
          <input name="applicantBirthDay"><input name="applicantAddress">
        </form>
      </div>
    </div></body></html>
    """


class FakeResponse:
    def __init__(self, url: str, html: str, status_code: int = 200):
        self.url = url
        self.content = html.encode("utf-8")
        self.text = html
        self.status_code = status_code
        self.history: list[object] = []


class FakeSession:
    def close(self) -> None:
        return None


class SyntheticBackend:
    def __init__(
        self,
        *,
        duplicate_identity: bool = False,
        bad_overflow_menu: str = "",
        unstable_menu: str = "",
        detail_title_mismatch: str = "",
        omit_application: str = "",
    ):
        self.courses = _branch_courses()
        if duplicate_identity:
            first_menu = jeongeup.JEONGEUP_BRANCHES[0].list_menu
            other_menu = jeongeup.JEONGEUP_BRANCHES[2].list_menu
            duplicate = self.courses[first_menu][0].identity
            self.courses[other_menu][-1] = replace(
                self.courses[other_menu][-1], identity=duplicate
            )
        self.bad_overflow_menu = bad_overflow_menu
        self.unstable_menu = unstable_menu
        self.detail_title_mismatch = detail_title_mismatch
        self.omit_application = omit_application
        self.urls: list[str] = []
        self.counts: Counter[tuple[str, int]] = Counter()
        self.lock = Lock()

    def response(self, url: str) -> FakeResponse:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        menu = query.get("menuCd", [""])[0]
        with self.lock:
            self.urls.append(url)
        if menu in jeongeup.JEONGEUP_BRANCH_BY_LIST_MENU:
            branch = jeongeup.JEONGEUP_BRANCH_BY_LIST_MENU[menu]
            requested = int(query.get("startPage", ["1"])[0])
            courses = self.courses[menu]
            last = max(
                1,
                (len(courses) + jeongeup.JEONGEUP_PAGE_SIZE - 1)
                // jeongeup.JEONGEUP_PAGE_SIZE,
            )
            with self.lock:
                self.counts[(menu, requested)] += 1
                request_count = self.counts[(menu, requested)]
            unstable = (
                self.unstable_menu == menu
                and requested == 1
                and request_count >= 2
            )
            html = _list_html(
                branch,
                courses,
                requested,
                bad_overflow=self.bad_overflow_menu == menu and requested == last + 1,
                title_suffix=" 변경" if unstable else "",
            )
            return FakeResponse(url, html)
        if menu in jeongeup.JEONGEUP_BRANCH_BY_DETAIL_MENU:
            branch = jeongeup.JEONGEUP_BRANCH_BY_DETAIL_MENU[menu]
            identity = query.get("reUniqId", [""])[0]
            course = next(
                item
                for item in self.courses[branch.list_menu]
                if item.identity == identity
            )
            page = (
                self.courses[branch.list_menu].index(course)
                // jeongeup.JEONGEUP_PAGE_SIZE
                + 1
            )
            return FakeResponse(
                url,
                _detail_html(
                    branch,
                    course,
                    page,
                    title_suffix=" 불일치" if identity == self.detail_title_mismatch else "",
                    omit_application=identity == self.omit_application,
                ),
            )
        return FakeResponse(url, "not found", status_code=404)


def _fetch(backend: SyntheticBackend):
    def fetcher(_session: object, url: str, _timeout: int) -> FakeResponse:
        return backend.response(url)

    return fetcher


def _collect(backend: SyntheticBackend, **options):
    return jeongeup.collect_jeongeup_education(
        TARGET,
        today="2026-07-23",
        session_factory=FakeSession,
        fetcher=_fetch(backend),
        max_workers=4,
        **options,
    )


def test_identity_hashes_target_and_owner_boundaries() -> None:
    canonical = jeongeup.JEONGEUP_CANONICAL_URL
    assert jeongeup.JEONGEUP_PROVIDER == (
        "MUNI_WWW_JEONGEUP_GO_KR_" + sha1(canonical.encode()).hexdigest()[:8].upper()
    )
    assert jeongeup.JEONGEUP_CANONICAL_CANDIDATE_ID == (
        "MUNI_IR_" + sha256(canonical.encode()).hexdigest()[:12].upper()
    )
    assert jeongeup.JEONGEUP_CANONICAL_URL_SHA256 == sha256(
        canonical.encode()
    ).hexdigest()
    assert jeongeup.is_target(TARGET)
    assert not jeongeup.is_target(
        {**TARGET, "url": canonical + "&startPage=1"}
    )
    assert not jeongeup.is_target(
        {**TARGET, "provider": "MUNI_WWW_JEONGEUP_GO_KR_BBC04A35"}
    )
    assert not jeongeup.is_target(
        {**TARGET, "url": canonical.replace("https://", "http://")}
    )

    audit = jeongeup.JEONGEUP_CANDIDATE_AUDIT
    canonical_audit = audit[jeongeup.JEONGEUP_CANONICAL_CANDIDATE_ID]
    assert canonical_audit["url_sha256"] == jeongeup.JEONGEUP_CANONICAL_URL_SHA256
    review = audit[jeongeup.JEONGEUP_REVIEW_SITEMAP_CANDIDATE_ID]
    assert review["provider"] == "MUNI_WWW_JEONGEUP_GO_KR_BBC04A35"
    assert review["redirect_destination"] == jeongeup.JEONGEUP_REVIEW_SITEMAP_DESTINATION
    assert "exclude_redirected_culture_whole_menu" in review["decision"]
    assert len(jeongeup.JEONGEUP_BRANCHES) == 4
    assert all(branch.candidate_id in audit for branch in jeongeup.JEONGEUP_BRANCHES)
    assert jeongeup.JEONGEUP_BRANCHES[0].category_options[3] == (
        "003",
        "성인진로역량개발",
    )
    boundary_urls = " ".join(item["url"] for item in jeongeup.JEONGEUP_OWNER_BOUNDARIES)
    for fragment in ("spt.jeongeup.go.kr", "lib.jeongeup.go.kr", "202001000000", "facilitie"):
        assert fragment in boundary_urls


def test_operational_arguments_require_complete_four_branch_snapshot() -> None:
    from Crawler import Crawler_GeneratedYamlTargets as generated

    arguments = generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[
        jeongeup.JEONGEUP_PROVIDER
    ]
    parsed = generated.parse_args(arguments)

    assert parsed.save_db is True
    assert parsed.mark_stale is True
    assert parsed.allow_partial_save is False
    assert parsed.per_target_limit == 0
    assert parsed.max_pages == 40
    assert parsed.detail_limit == 50


def test_back_control_allows_source_context_or_canonical_detail_default() -> None:
    branch = jeongeup.JEONGEUP_BRANCHES[0]
    listed = {
        "identity": "RE0001263",
        "branch": branch,
        "page": 2,
    }
    source_href = _pager_href(branch, 2)
    default_href = _pager_href(branch, 1).replace(
        "orderSort=asc",
        "orderSort=desc",
    )
    for href in (source_href, default_href):
        node = jeongeup.BeautifulSoup(
            f'<a href="{href}">목록</a>',
            "html.parser",
        ).a
        jeongeup._validate_back_control(node, listed)

    unsafe = jeongeup.BeautifulSoup(
        f'<a href="{default_href.replace("startPage=1", "startPage=2")}">'
        "목록</a>",
        "html.parser",
    ).a
    with pytest.raises(jeongeup.JeongeupContractError):
        jeongeup._validate_back_control(unsafe, listed)


def test_complete_four_branch_snapshot_boundaries_controls_and_privacy() -> None:
    backend = SyntheticBackend()
    rows, parser, meta = _collect(backend)
    assert parser == jeongeup.JEONGEUP_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["existing_active_owner_count"] == 0
    assert meta["branch_count"] == 4
    assert meta["advertised_total"] == 40
    assert meta["data_pages"] == 7
    assert meta["advertised_last_pages"] == {
        "평생학습관": 2,
        "정읍 단풍아카데미": 1,
        "청소년문화체육관": 3,
        "청소년상담복지센터": 1,
    }
    assert meta["page_counts"] == {
        "평생학습관": [10, 3],
        "정읍 단풍아카데미": [4],
        "청소년문화체육관": [10, 10, 1],
        "청소년상담복지센터": [2],
    }
    assert meta["overflow_pages"] == {
        "평생학습관": 3,
        "정읍 단풍아카데미": 2,
        "청소년문화체육관": 4,
        "청소년상담복지센터": 2,
    }
    assert meta["source_rows"] == 40
    assert meta["source_total"] == 40
    assert sum(meta["source_branch_counts"].values()) == 40
    assert len(
        {
            course.identity
            for courses in backend.courses.values()
            for course in courses
        }
    ) == 40
    assert meta["current_source_count"] == 5
    assert meta["expired_source_count"] == 35
    assert meta["source_requests"] == 28
    assert meta["request_attempts"] == 28
    assert meta["list_requests"] == 23
    assert meta["detail_pages"] == 5
    assert meta["boundary_rechecks"] == 12
    assert meta["overflow_clamp_verified"]
    assert meta["page1_rechecked"]
    assert meta["last_pages_rechecked"]
    assert meta["overflow_rechecked"]
    assert meta["pagination_complete"]
    assert meta["branch_boundaries_complete"]
    assert meta["details_complete"]
    assert meta["snapshot_complete"]
    assert meta["full_snapshot_validated"]
    assert meta["privacy_violations"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert len(rows) == 5
    assert Counter(row["status"] for row in rows) == {
        "CLOSED": 3,
        "OPEN": 1,
        "SCHEDULED": 1,
    }
    assert meta["application_control_count"] == 1
    assert meta["actionable_application_count"] == 1
    assert all(row["description"] == row["title"] for row in rows)
    assert all(row["address"] == row["venue_address"] == "" for row in rows)
    assert all(
        bool(row["application_url"]) == (row["status"] == "OPEN")
        for row in rows
    )
    assert all(row["raw_fields"]["application_form_submitted"] is False for row in rows)
    payload = repr(rows)
    for forbidden in (
        "저장하면 안 되는 개인강사",
        "저장하면 안 되는 담당자",
        "063-539-1234",
        "저장하면 안 되는 자유 서술 본문",
        "개인자료.hwp",
        "applicantName",
        "applicantPhone",
    ):
        assert forbidden not in payload
    assert not any("applicationSubmit" in url for url in backend.urls)
    assert not any("material.hwp" in url for url in backend.urls)
    assert all(urlparse(url).path == jeongeup.JEONGEUP_PATH for url in backend.urls)


@pytest.mark.parametrize(
    ("backend", "error_fragment"),
    (
        (SyntheticBackend(duplicate_identity=True), "identity set"),
        (
            SyntheticBackend(
                bad_overflow_menu=jeongeup.JEONGEUP_BRANCHES[0].list_menu
            ),
            "did not clamp exactly",
        ),
        (
            SyntheticBackend(unstable_menu=jeongeup.JEONGEUP_BRANCHES[0].list_menu),
            "page-one stability failed",
        ),
        (
            SyntheticBackend(detail_title_mismatch="RE9000040"),
            "title drift",
        ),
        (
            SyntheticBackend(omit_application="RE9000040"),
            "open detail control drift",
        ),
    ),
)
def test_contract_drift_fails_closed(
    backend: SyntheticBackend,
    error_fragment: str,
) -> None:
    rows, _, meta = _collect(backend)
    assert rows == []
    assert error_fragment in meta["configured_collection_error"]
    assert not meta["snapshot_complete"]


def test_caps_managed_session_and_response_url_fail_closed() -> None:
    rows, _, meta = jeongeup.collect_jeongeup_education(
        TARGET,
        today="2026-07-23",
    )
    assert rows == []
    assert meta["configured_collection_error"] == "managed session_factory injection is required"

    rows, _, meta = _collect(SyntheticBackend(), max_pages=6)
    assert rows == []
    assert meta["source_cap_reached"]
    assert "exceed max_pages" in meta["configured_collection_error"]

    rows, _, meta = _collect(SyntheticBackend(), detail_limit=4)
    assert rows == []
    assert meta["source_cap_reached"]
    assert "detail_limit" in meta["configured_collection_error"]
    assert meta["detail_pages"] == 0

    class UrlDriftBackend(SyntheticBackend):
        def response(self, url: str) -> FakeResponse:
            response = super().response(url)
            response.url = url.replace("www.jeongeup.go.kr", "jeongeup.go.kr")
            return response

    rows, _, meta = _collect(UrlDriftBackend())
    assert rows == []
    assert "response URL drift" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_JEONGEUP_LIVE") != "1",
    reason="set RUN_JEONGEUP_LIVE=1 for the bounded official-source audit",
)
def test_live_exact_audit_baseline_and_redirected_review_candidate() -> None:
    review = requests.get(
        jeongeup.JEONGEUP_REVIEW_SITEMAP_URL,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
        allow_redirects=False,
    )
    assert review.status_code == 302
    assert urljoin(review.url, review.headers["Location"]) == (
        jeongeup.JEONGEUP_REVIEW_SITEMAP_DESTINATION
    )

    rows, parser, meta = jeongeup.collect_jeongeup_education(
        TARGET,
        today="2026-07-23",
        allow_raw_requests_for_tests=True,
        timeout=30,
        max_pages=40,
        detail_limit=50,
        max_workers=4,
    )
    assert parser == jeongeup.JEONGEUP_PARSER
    assert meta["configured_collection_error"] == ""
    assert len(rows) == 7
    assert meta["source_requests"] == 48
    assert meta["request_attempts"] == 48
    assert meta["list_requests"] == 41
    assert meta["detail_pages"] == 7
    assert meta["advertised_total"] == 236
    assert meta["data_pages"] == 25
    assert meta["advertised_last_pages"] == {
        "평생학습관": 8,
        "정읍 단풍아카데미": 2,
        "청소년문화체육관": 12,
        "청소년상담복지센터": 3,
    }
    assert meta["page_counts"] == {
        "평생학습관": [10] * 7 + [7],
        "정읍 단풍아카데미": [10, 6],
        "청소년문화체육관": [10] * 11 + [9],
        "청소년상담복지센터": [10, 10, 4],
    }
    assert meta["overflow_pages"] == {
        "평생학습관": 9,
        "정읍 단풍아카데미": 3,
        "청소년문화체육관": 13,
        "청소년상담복지센터": 4,
    }
    assert meta["source_rows"] == 236
    assert meta["source_identity_numeric_min"] == 660
    assert meta["source_identity_numeric_max"] == 1249
    assert meta["source_status_counts"] == {
        "교육종료": 229,
        "교육중": 5,
        "접수완료": 2,
    }
    assert meta["source_branch_counts"] == {
        "평생학습관": 77,
        "정읍 단풍아카데미": 16,
        "청소년문화체육관": 119,
        "청소년상담복지센터": 24,
    }
    assert meta["current_source_count"] == 7
    assert meta["expired_source_count"] == 229
    assert meta["current_source_branch_counts"] == {
        "평생학습관": 3,
        "청소년문화체육관": 3,
        "청소년상담복지센터": 1,
    }
    assert meta["current_source_status_counts"] == {"교육중": 5, "접수완료": 2}
    assert meta["status_counts"] == {"CLOSED": 7}
    assert meta["application_control_count"] == 0
    assert meta["boundary_rechecks"] == 12
    assert meta["overflow_clamp_verified"]
    assert meta["branch_boundaries_complete"]
    assert meta["details_complete"]
    assert meta["privacy_violations"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert meta["snapshot_complete"]
    assert {row["provider_course_id"] for row in rows} == {
        f"{jeongeup.JEONGEUP_PROVIDER}:re:{identity}"
        for identity in (
            "RE0001233",
            "RE0001234",
            "RE0001235",
            "RE0001196",
            "RE0001199",
            "RE0001248",
            "RE0001249",
        )
    }
    assert all(row["description"] == row["title"] for row in rows)
    assert all(row["address"] == row["venue_address"] == "" for row in rows)
