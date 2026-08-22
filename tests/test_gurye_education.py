from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
import os
import threading
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from Crawler import municipal_gurye as gurye


TARGET = {"provider": gurye.GURYE_PROVIDER, "url": gurye.GURYE_LIST_URL}


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    office: str
    venue: str
    event_start: str
    event_end: str
    status: str
    status_class: str
    method: str = "오프라인접수"
    apply_start: str = "2026-01-02 9시"
    apply_end: str = "2026-06-30 18시"
    schedule: str = "매주 화요일 10:00~12:00"
    fee: str = "무료"
    capacity: int = 20
    target: str = "구례군민"


class Response:
    def __init__(
        self,
        body: str | bytes,
        url: str,
        *,
        status_code: int = 200,
        content_type: str = "text/html;charset=UTF-8",
        final_url: str | None = None,
        history: tuple[Any, ...] = (),
    ) -> None:
        self.status_code = status_code
        self.content = body.encode("utf-8") if isinstance(body, str) else body
        self.url = final_url or url
        self.headers = {"Content-Type": content_type}
        self.history = history


class DummySession:
    def close(self) -> None:
        return None


def _courses() -> list[Course]:
    current = [
        Course(
            "YEYAK_0000000367",
            "2026년 하반기 목공예 기능인 양성지원 교육생 모집",
            "목재문화체험장",
            "구례목재문화체험장",
            "2026-07-01",
            "2026-09-05",
            "진행중",
            "lecture",
            method="온/오프라인접수",
        ),
        Course(
            "YEYAK_0000000364",
            "여성문화회관 프로그램 왕초보 옷 만들기와 수선",
            "평생교육과",
            "구례여성문화회관",
            "2026-05-01",
            "2026-07-21",
            "진행중",
            "lecture",
        ),
        Course(
            "YEYAK_0000000356",
            "2026년 상반기 평생교육 프로그램 교육생 모집",
            "평생교육과",
            "구례군 평생학습관",
            "2026-05-01",
            "2026-08-28",
            "진행중",
            "lecture",
        ),
        Course(
            "YEYAK_0000000351",
            "2026년 야생화 활용 보존화 교육",
            "농업기술센터",
            "압화체험교육관",
            "2026-05-01",
            "2026-07-22",
            "진행중",
            "lecture",
        ),
        Course(
            "YEYAK_0000000363",
            "여성문화회관 건강교실 라인댄스",
            "평생교육과",
            "여성문화회관 체육실",
            "2026-05-01",
            "2026-11-19",
            "진행중",
            "lecture",
        ),
        Course(
            "YEYAK_0000000366",
            "여성문화회관 프로그램 요가 운영",
            "평생교육과",
            "여성문화회관 체육실",
            "2026-05-01",
            "2026-11-19",
            "진행중",
            "lecture",
        ),
        Course(
            "YEYAK_0000000348",
            "2026년 구례군종합사회복지관 프로그램 수강생모집",
            "평생교육과",
            "구례군 종합사회복지관",
            "2026-05-01",
            "2026-12-24",
            "진행중",
            "lecture",
        ),
    ]
    expired_offices = [
        "목재문화체험장",
        "평생교육과",
        "평생교육과",
        "평생교육과",
        "평생교육과",
        "농업기술센터",
        "매천도서관",
        "매천도서관",
        "지리산정원관리사업소",
        "지리산정원관리사업소",
        "지리산정원관리사업소",
    ]
    expired = [
        Course(
            f"YEYAK_{identity:010d}",
            f"종료 교육 {index}",
            office,
            "구례군청 교육장",
            "2026-01-01",
            "2026-06-30",
            "종료",
            "finish",
        )
        for index, (identity, office) in enumerate(zip(range(347, 336, -1), expired_offices, strict=True), start=1)
    ]
    return current + expired


def _category_tabs() -> str:
    return "".join(
        (
            '<a href="/yeyak/YeyakList.do?'
            f"{urlencode({'searchTrainingCaCode': code, 'menuNo': gurye.GURYE_MENU_NO})}"
            f'">{escape(label)}</a>'
        )
        for code, label in gurye.GURYE_CATEGORY_CODES.items()
    )


def _control(course: Course) -> str:
    if course.method == "오프라인접수":
        return (
            '<a class="disable" href="javascript:void(0)" '
            "onclick=\"javascript:alert('오프라인 접수 입니다. 전화문의 바랍니다.');\">"
            "신청하기</a>"
        )
    return '<a class="disable" href="javascript:void(0)">신청하기</a>'


def _card(course: Course, *, identity: str | None = None) -> str:
    source_identity = identity or course.identity
    detail_url = gurye.gurye_detail_url(source_identity)
    return f"""
      <div class="applyProgram">
        <h5>{escape(course.title)}</h5>
        <div class="tag">
          <span class="{course.status_class}">{course.status}</span>
          <span>{course.office}</span><span>{course.method}</span>
        </div>
        <div class="info"><ul class="list">
          <li><em>접수기간</em><span>{course.apply_start} ~ {course.apply_end}</span></li>
          <li><em>수강기간</em><span>{course.event_start} ~ {course.event_end}</span></li>
          <li><em>수강시간</em><span>{course.schedule}</span></li>
          <li><em>수강료</em><span>{course.fee}</span></li>
          <li><em>교육장소</em><span>{course.venue}</span></li>
          <li><em>모집인원</em><span>{course.capacity}</span></li>
        </ul></div>
        <div class="btn">
          <span class="view"><a href="{detail_url}">상세보기</a></span>
          <span class="apply">{_control(course)}</span>
        </div>
      </div>
    """


def _page_html(
    courses: list[Course],
    page: int,
    *,
    mode: str = "normal",
    sentinel_recheck: bool = False,
) -> str:
    if mode == "premature_short" and page == 1:
        visible = courses[:9]
    elif page == 1:
        visible = courses[:10]
    elif page == 2:
        visible = courses[10:]
    else:
        visible = []
    if mode == "sentinel_unstable" and page == 3 and sentinel_recheck:
        visible = [courses[-1]]
    cards: list[str] = []
    for index, course in enumerate(visible):
        identity = None
        if mode == "duplicate" and page == 2 and index == 0:
            identity = courses[0].identity
        cards.append(_card(course, identity=identity))
    catalogue = "".join(cards) if cards else f'<div class="applyProgram">{gurye._EMPTY_MARKER}</div>'
    pager = "".join(f"<li><a>{value}</a></li>" for value in (1, 2))
    return f"""
      <html><head><title>{gurye._TITLE}</title></head><body>
        <div id="content">
          {_category_tabs()}
          <form id="articleForm" action="{gurye.GURYE_LIST_PATH}">
            <input name="menuNo" value="{gurye.GURYE_MENU_NO}">
            <input name="searchTrainingCaCode" value="">
          </form>
          {catalogue}
          <ul class="paging">{pager}</ul>
        </div>
      </body></html>
    """


def _detail_html(
    course: Course,
    *,
    title: str | None = None,
    target: str | None = None,
) -> str:
    if course.method == "오프라인접수":
        control = _control(course)
    else:
        control = ""
    return f"""
      <html><head><title>{gurye._TITLE}</title></head><body>
        <div id="content">
          <div class="applyView_tit"><div class="applySearch_wrap">
            <span class="state">{course.status}</span>
            <h3>{escape(title or course.title)}</h3>
          </div></div>
          <div class="applyView"><ul class="list">
            <li><em>교육과정구분</em><span>{course.office} &gt; {escape(course.title)}</span></li>
            <li><em>모집인원</em><span>{course.capacity}명</span></li>
            <li><em>접수기간</em><span>{course.apply_start} ~ {course.apply_end}</span></li>
            <li><em>수강기간</em><span>{course.event_start} ~ {course.event_end}</span></li>
            <li><em>수강시간</em><span>{course.schedule}</span></li>
            <li><em>교육대상</em><span>{escape(target or course.target)}</span></li>
            <li><em>교육장소</em><span>{course.venue}</span></li>
            <li><em>수강료</em><span>{course.fee}</span></li>
            <li><em>문의전화</em><span>discarded by collector</span></li>
          </ul></div>
          <div class="apply_btn">{control}</div>
        </div>
      </body></html>
    """


class FixtureFetcher:
    def __init__(self, courses: list[Course], *, mode: str = "normal") -> None:
        self.courses = courses
        self.mode = mode
        self.calls: list[str] = []
        self.counts: dict[str, int] = {}
        self.lock = threading.Lock()

    def __call__(self, _session: Any, url: str, _timeout: int) -> Response:
        with self.lock:
            self.calls.append(url)
            self.counts[url] = self.counts.get(url, 0) + 1
            request_count = self.counts[url]
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == gurye.GURYE_LIST_PATH:
            page = int((query.get("pageIndex") or ["1"])[0])
            body = _page_html(
                self.courses,
                page,
                mode=self.mode,
                sentinel_recheck=(page == 3 and request_count > 1),
            )
            return Response(body, url)
        if parsed.path == gurye.GURYE_DETAIL_PATH:
            identity = (query.get("trainingId") or [""])[0]
            course = next(item for item in self.courses if item.identity == identity)
            title = course.title + " 변경" if self.mode == "detail_mismatch" else None
            target = "담당자 061-123-4567" if self.mode == "pii_target" else None
            return Response(_detail_html(course, title=title, target=target), url)
        raise AssertionError(f"unexpected application or external request: {url}")


def _collect(
    *,
    mode: str = "normal",
    courses: list[Course] | None = None,
    detail_limit: int = 100,
) -> tuple[list[dict[str, Any]], dict[str, Any], FixtureFetcher]:
    source = courses or _courses()
    fetcher = FixtureFetcher(source, mode=mode)
    rows, parser, meta = gurye.collect_gurye_education(
        TARGET,
        cutoff=date(2026, 7, 21),
        workers=3,
        detail_limit=detail_limit,
        session_factory=DummySession,
        html_fetcher=fetcher,
    )
    assert parser == gurye.GURYE_PARSER
    return rows, meta, fetcher


def test_discovery_audit_and_owner_boundaries_are_exact() -> None:
    audit = gurye.GURYE_DISCOVERY_AUDIT
    assert gurye.GURYE_MUNICIPALITY_CODE == "1273000000"
    assert gurye.GURYE_LIST_URL == (
        "https://www.gurye.go.kr/yeyak/YeyakList.do?searchTrainingCaCode=&menuNo=119001001000"
    )
    assert audit["source_total"] == 18
    assert audit["page_counts"] == [10, 8]
    assert audit["current_or_future"] == 7
    assert audit["status_counts"] == {"진행중": 7, "종료": 11}
    assert gurye.GURYE_CANDIDATE_AUDIT[gurye.GURYE_STALE_CANDIDATE_ID]["decision"].startswith("replace_stale")
    assert gurye.GURYE_OWNER_BOUNDARY_AUDIT["gurye_family_center"]["decision"] == "keep_separate_family_center_owner"
    assert "sansuyu.go.kr" not in gurye.GURYE_LIST_URL


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        (gurye.GURYE_STALE_PROVIDER, gurye.GURYE_STALE_URL),
        (gurye.GURYE_PROVIDER, gurye.GURYE_AGRICULTURAL_ALIAS_URL),
        (gurye.GURYE_PROVIDER, gurye.GURYE_FAMILY_CENTER_URL),
        (gurye.GURYE_PROVIDER, gurye.GURYE_LIST_URL + "#fragment"),
        ("wrong-provider", gurye.GURYE_LIST_URL),
    ],
)
def test_only_exact_county_owner_catalogue_is_accepted(provider: str, url: str) -> None:
    assert gurye.is_gurye_education_target({"provider": provider, "url": url}) is False
    rows, parser, meta = gurye.collect_gurye_education(
        {"provider": provider, "url": url},
        session_factory=lambda: pytest.fail("rejected target must not fetch"),
    )
    assert rows == []
    assert parser == gurye.GURYE_PARSER
    assert meta["pagination_complete"] is False
    assert meta["list_requests"] == 0
    assert meta["configured_collection_error"]


def test_exact_target_matcher_is_router_ready() -> None:
    assert gurye.is_gurye_education_target(TARGET) is True
    assert gurye.is_target(TARGET) is True


def test_complete_walk_filter_detail_controls_and_exact_branches() -> None:
    rows, meta, fetcher = _collect()

    assert meta["pagination_complete"] is True
    assert meta["pages"] == 2
    assert meta["page_counts"] == {1: 10, 2: 8}
    assert meta["empty_sentinel_page"] == 3
    assert meta["declared_pager_max"] == 2
    assert meta["source_total"] == 18
    assert meta["source_status_counts"] == {"진행중": 7, "종료": 11}
    assert meta["current_source_count"] == 7
    assert meta["expired_source_count"] == 11
    assert meta["list_requests"] == 6
    assert meta["detail_verified"] == 7
    assert meta["active_application_controls"] == 0
    assert meta["application_form_requests"] == 0
    assert meta["branch_counts"] == {
        "구례목재문화체험장": 1,
        "구례여성문화회관": 3,
        "구례군 평생학습관": 1,
        "압화체험교육관": 1,
        "구례군 종합사회복지관": 1,
    }
    assert [row["raw_fields"]["source_identity"] for row in rows] == [
        "YEYAK_0000000367",
        "YEYAK_0000000364",
        "YEYAK_0000000356",
        "YEYAK_0000000351",
        "YEYAK_0000000363",
        "YEYAK_0000000366",
        "YEYAK_0000000348",
    ]
    assert all(row["category"] == "교육" for row in rows)
    assert all(row["reservation_available"] is False for row in rows)
    assert all(row["application_url"] == "" for row in rows)
    assert all(row["preserve_branch"] is True for row in rows)
    assert all("문의전화" not in str(row) for row in rows)
    assert len(fetcher.calls) == 13
    assert all("Apply" not in url and "login" not in url.lower() for url in fetcher.calls)


@pytest.mark.parametrize(
    ("mode", "error"),
    [
        ("duplicate", "duplicate identities"),
        ("sentinel_unstable", "stability check changed"),
        ("premature_short", "premature short page"),
        ("detail_mismatch", "title/status mismatch"),
        ("pii_target", "unsafe target"),
    ],
)
def test_contract_changes_fail_closed_without_partial_rows(mode: str, error: str) -> None:
    rows, meta, _fetcher = _collect(mode=mode)
    assert rows == []
    assert meta["pagination_complete"] is False
    assert meta["output_rows"] == 0
    assert error in meta["configured_collection_error"]


def test_detail_limit_fails_closed_before_any_detail_request() -> None:
    rows, meta, fetcher = _collect(detail_limit=6)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["pagination_complete"] is False
    assert "partial snapshot" in meta["configured_collection_error"]
    assert all(urlparse(url).path == gurye.GURYE_LIST_PATH for url in fetcher.calls)


def test_duplicate_rows_after_external_dedupe_are_not_silently_invented() -> None:
    fetcher = FixtureFetcher(_courses())

    rows, _parser, meta = gurye.collect_gurye_education(
        TARGET,
        cutoff=date(2026, 7, 21),
        session_factory=DummySession,
        html_fetcher=fetcher,
        dedupe_rows=lambda values: values[:1],
    )

    assert len(rows) == 1
    assert meta["detail_verified"] == 7
    assert meta["output_rows"] == 1


@pytest.mark.skipif(
    os.environ.get("GURYE_EDUCATION_LIVE") != "1",
    reason="set GURYE_EDUCATION_LIVE=1 for the opt-in official-site check",
)
def test_live_official_catalogue_snapshot() -> None:
    rows, parser, meta = gurye.collect_gurye_education(
        TARGET,
        cutoff=date(2026, 7, 21),
        timeout=30,
    )
    assert parser == gurye.GURYE_PARSER
    assert meta["pagination_complete"] is True, meta
    assert meta["source_total"] == 18
    assert meta["page_counts"] == {1: 10, 2: 8}
    assert meta["current_source_count"] == 7
    assert meta["detail_verified"] == 7
    assert len(rows) == 7
