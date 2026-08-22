from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
import math
import os
from threading import Lock
import time
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from Crawler import municipal_gwangyang as gwangyang


@dataclass
class Target:
    provider: str = gwangyang.GWANGYANG_PROVIDER
    url: str = gwangyang.GWANGYANG_CANONICAL_URL
    candidate_id: str = ""


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    source_key: str
    status: str = "CLOSED"
    apply_start: str = "2025-01-01 09:00"
    apply_end: str = "2025-01-10 18:00"
    start: str = "2025-02-01"
    end: str = "2025-03-01"
    schedule: str = "월, 수 10:00~12:00"
    selection: str = "선착순"
    capacity_current: int = 4
    capacity_total: int = 20
    waitlist_count: int = 0
    target: str = "광양시민"
    venue: str = "교육장 1실"
    fee: str = "10,000"
    single_date: bool = False
    missing_legacy_state: bool = False


class DummySession:
    def close(self) -> None:
        return None


def _fixture_courses() -> dict[str, list[Course]]:
    courses: dict[str, list[Course]] = {
        source.key: [] for source in gwangyang.GWANGYANG_SOURCES
    }
    women = gwangyang.GWANGYANG_SOURCE_BY_KEY["women_culture"]
    courses[women.key] = [
        Course(
            "1011",
            "현재 접수 강좌",
            women.key,
            status="OPEN",
            apply_start="2026-07-01 09:00",
            apply_end="2026-07-31 18:00",
            start="2026-08-01",
            end="2026-09-30",
            capacity_current=9,
            capacity_total=11,
            waitlist_count=2,
            venue="여성문화센터 302호",
            fee="30,000 (재료비 : 150,000)",
        )
    ] + [
        Course(str(identity), f"여성문화 과거 강좌 {identity}", women.key)
        for identity in range(1010, 1000, -1)
    ]
    courses["citizen_it"] = [
        Course(
            "2001",
            "현재 마감 정보화 강좌",
            "citizen_it",
            apply_start="2026-06-01 09:00",
            apply_end="2026-06-10 18:00",
            start="2026-07-01",
            end="2026-08-15",
            schedule="월~목(주4회) / 10:00~11:30",
            fee="0",
        )
    ]
    courses["digital_learning"] = [
        Course(
            "3002",
            "디지털 과거 단일일 강좌",
            "digital_learning",
            start="2025-02-07",
            end="2025-02-07",
            schedule="금 16:00~17:00",
            single_date=True,
            missing_legacy_state=True,
            selection="",
        ),
        Course("3001", "디지털 과거 범위 강좌", "digital_learning"),
    ]
    courses["resident_okgok"] = [
        Course("4001", "옥곡 과거 강좌", "resident_okgok")
    ]
    courses["resident_golyak"] = [
        Course("5001", "골약 과거 강좌", "resident_golyak")
    ]
    courses["resident_jungma"] = [
        Course(
            "6001",
            "접수 예정 주민 강좌",
            "resident_jungma",
            status="SCHEDULED",
            apply_start="2026-08-01 09:00",
            apply_end="2026-08-10 18:00",
            start="2026-09-01",
            end="2026-12-01",
            schedule="화, 목 19:00~21:00",
            fee="25,000원(월)",
        )
    ]
    courses["resident_geumho"] = [
        Course("8001", "금호 과거 강좌", "resident_geumho")
    ]
    courses["resident_gwangyeong"] = [
        Course("9001", "광영 과거 강좌", "resident_gwangyeong")
    ]
    courses["resident_gwangyang_eup"] = [
        Course("10001", "광양읍 과거 강좌", "resident_gwangyang_eup")
    ]
    return courses


def _source_query(source: gwangyang.GwangyangSource) -> str:
    query = [("mid", source.mid), ("even_cg", source.even_cg)]
    if source.edcc_cg:
        query.append(("edcc_cg", source.edcc_cg))
    return urlencode(query)


def _landing_html(*, missing_link: bool = False) -> str:
    links: list[str] = []
    for key in sorted(gwangyang.GWANGYANG_LANDING_DISCOVERY_KEYS):
        if missing_link and key == "resident_gwangyeong":
            continue
        source = gwangyang.GWANGYANG_SOURCE_BY_KEY[key]
        links.append(
            f'<a href="/lecture.es?{escape(_source_query(source), quote=True)}">'
            f"{escape(source.branch)}</a>"
        )
    return (
        "<html><head><title>강의안내/신청 : 평생학습도시</title></head>"
        f"<body>{''.join(links)}</body></html>"
    )


def _status_html(course: Course, source: gwangyang.GwangyangSource) -> str:
    if course.missing_legacy_state:
        return ""
    if course.status == "OPEN":
        href = f"/lectureMemberForm.es?mid={source.mid}&lec_seq={course.identity}"
        return (
            '<button class="btn_type nohover ing" '
            f'onclick="location.href=\'{escape(href, quote=True)}\'">접수가능</button>'
        )
    if course.status == "SCHEDULED":
        return (
            '<button class="btn_type nohover due" type="submit" '
            'onclick="accept_alert(); return false;">접수대기</button>'
        )
    return '<span class="btn_type nohover finish">접수마감</span>'


def _course_row(
    course: Course,
    source: gwangyang.GwangyangSource,
    number: int,
) -> str:
    detail = (
        f"/lecture.es?mid={source.mid}&lec_seq={course.identity}&act=view"
    )
    education_period = (
        course.start if course.single_date else f"{course.start} ~ {course.end}"
    )
    wait = f" ({course.waitlist_count})" if course.waitlist_count else ""
    return f"""
      <tr>
        <td class="num">{number}</td>
        <td class="title"><a class="subject" href="{escape(detail, quote=True)}"
          onclick="move_view({course.identity}); return false;">{escape(course.title)}</a></td>
        <td class="apply_date"><p class="lec_date date_start">{course.apply_start}</p>
          <p class="lec_date">~ {course.apply_end}</p></td>
        <td class="leccation_date"><p class="acc_date">{education_period}</p>
          {escape(course.schedule)}</td>
        <td class="leccation_way">{escape(course.selection)}</td>
        <td class="leccation_num">{course.capacity_current}/{course.capacity_total}{wait}</td>
        <td class="apply_way">인터넷<br></td>
        <td class="leccation_statue">{_status_html(course, source)}</td>
      </tr>
    """


def _list_html(
    source: gwangyang.GwangyangSource,
    courses: list[Course],
    page: int,
    *,
    nonempty_sentinel: bool = False,
    wrong_title: bool = False,
) -> str:
    total = len(courses)
    last = max(1, math.ceil(total / gwangyang.GWANGYANG_PAGE_SIZE))
    start = (page - 1) * gwangyang.GWANGYANG_PAGE_SIZE
    selected = courses[start : start + gwangyang.GWANGYANG_PAGE_SIZE]
    if page == last + 1 and nonempty_sentinel and courses:
        selected = courses[:1]
    body = "".join(
        _course_row(course, source, total - (start + index))
        for index, course in enumerate(selected)
    )
    if not selected:
        body = (
            '<tr><td colspan="8"><p class="no_result nodata">'
            "해당 날짜는 교육이 없습니다.</p></td></tr>"
        )
    edcc = escape(source.edcc_cg, quote=True)
    title = "잘못된 지점 | 교육/강좌" if wrong_title else source.page_title
    return f"""
      <html><head><title>{escape(title)}</title></head><body>
      <form id="infoForm" method="post" action="/lecture.es?mid={source.mid}">
        <input type="hidden" name="mid" value="{source.mid}">
        <input type="hidden" name="seq" value="">
        <input type="hidden" name="act" value="list">
        <input type="hidden" name="even_cg" value="{source.even_cg}">
        <input type="hidden" name="edcc_cg" value="{edcc}">
        <input type="hidden" name="nPage" value="{page}">
        <input type="text" name="keyWord" value="">
        <input type="hidden" name="_csrf" value="fixture-csrf-{page}">
      </form>
      <div class="bbs_info">총게시물 : {total:,} 건 페이지 : {page} /{last}</div>
      <table class="bbs_table">
        <thead><tr>
          <th>번호</th><th>강좌명</th><th>접수기간</th>
          <th>교육기간<br>교육요일/시간</th><th>선발방법</th>
          <th>신청/모집<br>(대기자)</th><th>신청방법</th><th>접수상태</th>
        </tr></thead>
        <tbody>{body}</tbody>
      </table>
      </body></html>
    """


def _detail_html(
    course: Course,
    source: gwangyang.GwangyangSource,
    *,
    wrong_title: bool = False,
    missing_open_button: bool = False,
    wrong_confirmation: bool = False,
    wrong_branch_title: bool = False,
) -> str:
    status_text, status_class = {
        "OPEN": ("접수중", "ing"),
        "SCHEDULED": ("접수예정", "due"),
        "CLOSED": ("접수마감", "finish"),
    }[course.status]
    heading_title = "다른 강좌" if wrong_title else course.title
    application_button = ""
    if course.status == "OPEN" and not missing_open_button:
        application_button = (
            '<div class="btn_go_apply active"><button '
            'class="btn_type go_apply btn_darkgray" onclick="beforeApply()">'
            "신청<br>하기</button></div>"
        )
    confirmation_id = "999999" if wrong_confirmation else course.identity
    education_period = (
        course.start if course.single_date else f"{course.start} ~ {course.end}"
    )
    wait = f" / 대기 {course.waitlist_count}명" if course.waitlist_count else ""
    fields = [
        ("접수기간", f"{course.apply_start} ~ {course.apply_end}"),
        (
            "접수현황",
            f"신청 {course.capacity_current}명 / 정원 {course.capacity_total}명{wait}",
        ),
        ("선발방법", course.selection),
        ("신청방법", "인터넷"),
        ("교육대상", course.target),
        ("교육기간", education_period),
        ("교육시간", course.schedule),
        ("교육장", course.venue),
        ("강사명", "홍길동"),
        ("수강료", course.fee),
        ("문의전화", "061-797-9999"),
        ("강의소개", "절대로 저장하면 안 되는 자유 서술과 private@example.com"),
        (
            "첨부파일",
            '<a href="/download.es?file=secret.hwp">강의계획서.hwp</a>',
        ),
    ]
    rows = "".join(
        f"<tr><th>{label}</th><td>{value if label == '첨부파일' else escape(value)}</td></tr>"
        for label, value in fields
    )
    page_title = "다른 지점" if wrong_branch_title else source.page_title
    return f"""
      <html><head><title>{escape(page_title)}</title></head><body>
        <div class="bbs_detail_tit"><h4>
          <span class="btn_type nohover {status_class}">{status_text}</span>
          {escape(heading_title)}
        </h4>{application_button}</div>
        <table class="bbs_table type02 mb_30">
          <caption>강좌조회 - 접수기간, 접수현황, 교육기간 순</caption>
          <tbody>{rows}</tbody>
        </table>
        <div class="confirmation-modal"><a class="btn_type"
          href="/lectureMemberForm.es?mid={source.mid}&amp;lec_seq={confirmation_id}">확인</a></div>
      </body></html>
    """


class FixtureSite:
    def __init__(
        self,
        *,
        courses: dict[str, list[Course]] | None = None,
        missing_landing_link: bool = False,
        nonempty_sentinel: str = "",
        wrong_list_title: str = "",
        wrong_detail_title: str = "",
        wrong_detail_branch: str = "",
        missing_open_button: bool = False,
        wrong_confirmation: bool = False,
        detail_capacity_override: tuple[int, int, int] | None = None,
        transient_url: str = "",
        always_fail_url: str = "",
    ) -> None:
        self.courses = courses or _fixture_courses()
        self.missing_landing_link = missing_landing_link
        self.nonempty_sentinel = nonempty_sentinel
        self.wrong_list_title = wrong_list_title
        self.wrong_detail_title = wrong_detail_title
        self.wrong_detail_branch = wrong_detail_branch
        self.missing_open_button = missing_open_button
        self.wrong_confirmation = wrong_confirmation
        self.detail_capacity_override = detail_capacity_override
        self.transient_url = transient_url
        self.always_fail_url = always_fail_url
        self.calls: Counter[str] = Counter()
        self.active = 0
        self.max_active = 0
        self.application_form_requests = 0
        self.lock = Lock()

    def fetch(self, _session: DummySession, url: str, _timeout: int) -> str:
        with self.lock:
            self.calls[url] += 1
            call = self.calls[url]
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.002)
            if url == self.always_fail_url:
                raise TimeoutError("persistent fixture timeout")
            if url == self.transient_url and call == 1:
                raise TimeoutError("one transient fixture timeout")
            if url == gwangyang.GWANGYANG_CANONICAL_URL:
                return _landing_html(missing_link=self.missing_landing_link)
            parsed = urlparse(url)
            query = parse_qs(parsed.query, keep_blank_values=True)
            if parsed.path == gwangyang.GWANGYANG_APPLICATION_PATH:
                self.application_form_requests += 1
                raise AssertionError("application form must never be fetched")
            if parsed.path != gwangyang.GWANGYANG_LIST_PATH:
                raise AssertionError(f"unexpected fixture URL: {url}")
            source = next(
                (
                    source
                    for source in gwangyang.GWANGYANG_SOURCES
                    if query.get("mid") == [source.mid]
                ),
                None,
            )
            if source is None:
                raise AssertionError(f"unknown fixture source: {url}")
            if query.get("act") == ["view"]:
                identity = query["lec_seq"][0]
                course = next(
                    course
                    for course in self.courses[source.key]
                    if course.identity == identity
                )
                if identity == "1011" and self.detail_capacity_override is not None:
                    current, total, wait = self.detail_capacity_override
                    course = replace(
                        course,
                        capacity_current=current,
                        capacity_total=total,
                        waitlist_count=wait,
                    )
                return _detail_html(
                    course,
                    source,
                    wrong_title=identity == self.wrong_detail_title,
                    missing_open_button=(
                        self.missing_open_button and course.status == "OPEN"
                    ),
                    wrong_confirmation=(
                        self.wrong_confirmation and course.status == "OPEN"
                    ),
                    wrong_branch_title=identity == self.wrong_detail_branch,
                )
            expected_even = [source.even_cg]
            expected_edcc = [source.edcc_cg] if source.edcc_cg else None
            if query.get("even_cg") != expected_even or (
                query.get("edcc_cg") != expected_edcc
            ):
                raise AssertionError(f"source filter missing from URL: {url}")
            page = int(query["nPage"][0])
            last = max(
                1,
                math.ceil(
                    len(self.courses[source.key]) / gwangyang.GWANGYANG_PAGE_SIZE
                ),
            )
            return _list_html(
                source,
                self.courses[source.key],
                page,
                nonempty_sentinel=(
                    source.key == self.nonempty_sentinel and page == last + 1
                ),
                wrong_title=source.key == self.wrong_list_title,
            )
        finally:
            with self.lock:
                self.active -= 1


def _collect(site: FixtureSite, **kwargs):
    return gwangyang.collect(
        Target(),
        today="2026-07-21",
        max_pages=41,
        detail_limit=3,
        max_workers=99,
        session_factory=DummySession,
        fetcher=site.fetch,
        **kwargs,
    )


def test_inventory_target_and_subset_ownership_contracts() -> None:
    assert len(gwangyang.GWANGYANG_SOURCES) == 10
    assert len({source.key for source in gwangyang.GWANGYANG_SOURCES}) == 10
    assert len({source.mid for source in gwangyang.GWANGYANG_SOURCES}) == 10
    assert [source.edcc_cg for source in gwangyang.GWANGYANG_SOURCES[3:]] == [
        f"EDCC00{number}" for number in range(1, 8)
    ]
    assert gwangyang.GWANGYANG_MUNICIPALITY_CODE == "1219000000"
    assert gwangyang.is_target(Target())
    assert not gwangyang.is_target(replace(Target(), provider="WRONG"))
    assert not gwangyang.is_target(
        replace(Target(), url=gwangyang.GWANGYANG_CANONICAL_URL + "&extra=1")
    )
    assert not gwangyang.is_target(
        replace(
            Target(),
            url="https://user:pass@gwangyang.go.kr/edu/menu.es?mid=b10300000000",
        )
    )
    assert gwangyang.is_gwangyang_subset_target(
        Target(
            provider=gwangyang.GWANGYANG_DIGITAL_SUBSET_PROVIDER,
            url=gwangyang.GWANGYANG_DIGITAL_SUBSET_URL,
        )
    )
    assert gwangyang.is_gwangyang_subset_target(
        Target(
            provider=gwangyang.GWANGYANG_RESIDENT_SUBSET_PROVIDER,
            url=gwangyang.GWANGYANG_RESIDENT_SUBSET_URL,
            candidate_id=gwangyang.GWANGYANG_RESIDENT_SUBSET_CANDIDATE_ID,
        )
    )
    assert (
        gwangyang.GWANGYANG_EXISTING_TARGET_AUDIT[
            gwangyang.GWANGYANG_DIGITAL_SUBSET_PROVIDER
        ]["owner"]
        == gwangyang.GWANGYANG_PROVIDER
    )


def test_url_builders_keep_every_source_filter_and_course_identity() -> None:
    source = gwangyang.GWANGYANG_SOURCE_BY_KEY["resident_jungma"]
    parsed = urlparse(gwangyang.gwangyang_list_url(source, 7))
    assert parsed.scheme == "https" and parsed.hostname == gwangyang.GWANGYANG_HOST
    assert parse_qs(parsed.query) == {
        "mid": [source.mid],
        "even_cg": [source.even_cg],
        "edcc_cg": [source.edcc_cg],
        "nPage": ["7"],
    }
    assert parse_qs(urlparse(gwangyang.gwangyang_detail_url(source, "1554")).query) == {
        "mid": [source.mid],
        "lec_seq": ["1554"],
        "act": ["view"],
    }
    assert parse_qs(
        urlparse(gwangyang.gwangyang_application_url(source, "1554")).query
    ) == {"mid": [source.mid], "lec_seq": ["1554"]}
    with pytest.raises(ValueError):
        gwangyang.gwangyang_list_url(source, 0)
    with pytest.raises(ValueError):
        gwangyang.gwangyang_detail_url(source, "../1554")


def test_complete_aggregate_sentinels_rechecks_details_and_privacy() -> None:
    transient = gwangyang.gwangyang_list_url("women_culture", 2)
    site = FixtureSite(transient_url=transient)
    rows, parser, meta = _collect(site)

    assert parser == gwangyang.GWANGYANG_PARSER
    assert len(rows) == 3
    assert {row["status"] for row in rows} == {"OPEN", "CLOSED", "SCHEDULED"}
    assert {row["branch"] for row in rows} == {
        "여성문화센터",
        "시민정보화교육",
        "중마동 주민자치센터",
    }
    open_row = next(row for row in rows if row["status"] == "OPEN")
    assert open_row["reservation_available"] is True
    assert open_row["application_type"] == "ONLINE_RESERVATION_LOGIN_REQUIRED"
    assert open_row["application_url"] == gwangyang.gwangyang_application_url(
        "women_culture", "1011"
    )
    assert open_row["capacity_current"] == 9
    assert open_row["capacity_total"] == 11
    assert open_row["waitlist_count"] == 2
    assert open_row["fee_amount"] == 30000
    assert all(
        row["application_url"] == ""
        for row in rows
        if row["status"] != "OPEN"
    )
    assert all(row["program_type"] == "교육" for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["municipality_code"] == "1219000000" for row in rows)
    payload = repr(rows)
    assert "홍길동" not in payload
    assert "061-797-9999" not in payload
    assert "private@example.com" not in payload
    assert "강의계획서.hwp" not in payload
    assert "절대로 저장하면 안 되는" not in payload
    assert site.application_form_requests == 0

    assert meta["landing_verified"] is True
    assert meta["landing_source_links_verified"] == 4
    assert meta["source_count"] == 10
    assert meta["source_rows"] == 20
    assert meta["data_pages"] == 11
    assert meta["required_list_requests"] == 41
    assert meta["list_requests"] == 41
    assert meta["sentinel_verified_count"] == 10
    assert meta["stability_rechecks"] == 20
    assert meta["current_source_count"] == 3
    assert meta["expired_count"] == 17
    assert meta["single_education_date_count"] == 1
    assert meta["detail_attempts"] == meta["detail_pages"] == 3
    assert meta["application_control_count"] == 1
    assert meta["application_confirmation_links_verified"] == 3
    assert meta["pages"] == meta["request_count"] == 45
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["pii_payload_persisted"] is False
    assert meta["configured_collection_error"] == ""
    assert site.calls[transient] == 3  # data + last recheck + one retry
    assert 2 <= site.max_active <= gwangyang.GWANGYANG_MAX_WORKERS
    assert meta["network_concurrency"] == gwangyang.GWANGYANG_MAX_WORKERS


@pytest.mark.parametrize(
    ("site", "message"),
    [
        (FixtureSite(missing_landing_link=True), "lost official catalogue links"),
        (
            FixtureSite(nonempty_sentinel="women_culture"),
            "sentinel",
        ),
        (FixtureSite(wrong_list_title="resident_taein"), "branch title changed"),
        (FixtureSite(wrong_detail_title="1011"), "list/detail title mismatch"),
        (FixtureSite(wrong_detail_branch="2001"), "detail branch title changed"),
        (FixtureSite(missing_open_button=True), "lacks one application button"),
        (FixtureSite(wrong_confirmation=True), "confirmation application identity mismatch"),
    ],
)
def test_contract_drift_fails_the_whole_snapshot(
    site: FixtureSite, message: str
) -> None:
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_duplicate_identity_and_downstream_dedupe_cannot_publish() -> None:
    courses = _fixture_courses()
    courses["resident_golyak"] = [
        replace(courses["resident_golyak"][0], identity="4001")
    ]
    rows, _parser, meta = _collect(FixtureSite(courses=courses))
    assert rows == []
    assert "duplicate official identities" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(
        FixtureSite(), dedupe_rows=lambda values: values[:-1]
    )
    assert rows == []
    assert "dedupe changed official identity cardinality" in meta[
        "configured_collection_error"
    ]


def test_detail_capacity_snapshot_wins_when_live_counts_change_mid_crawl() -> None:
    rows, _parser, meta = _collect(
        FixtureSite(detail_capacity_override=(10, 12, 3))
    )

    assert meta["snapshot_complete"] is True
    open_row = next(
        row
        for row in rows
        if row["provider_course_id"] == f"{gwangyang.GWANGYANG_PROVIDER}:1011"
    )
    assert open_row["capacity_current"] == 10
    assert open_row["capacity_total"] == 12
    assert open_row["waitlist_count"] == 3
    assert open_row["raw_fields"]["list_capacity_current"] == 9
    assert open_row["raw_fields"]["list_capacity_total"] == 11
    assert open_row["raw_fields"]["list_waitlist_count"] == 2
    assert open_row["raw_fields"]["capacity_snapshot_changed"] is True
    assert open_row["raw_fields"]["capacity_snapshot_source"] == "detail"


def test_caps_stop_before_forbidden_partial_work() -> None:
    site = FixtureSite()
    rows, _parser, meta = gwangyang.collect(
        Target(),
        today="2026-07-21",
        max_pages=40,
        detail_limit=3,
        session_factory=DummySession,
        fetcher=site.fetch,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["required_list_requests"] == 41
    assert meta["list_requests"] == 10
    assert meta["detail_attempts"] == 0

    site = FixtureSite()
    rows, _parser, meta = gwangyang.collect(
        Target(),
        today="2026-07-21",
        max_pages=41,
        detail_limit=2,
        session_factory=DummySession,
        fetcher=site.fetch,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["pagination_complete"] is True
    assert meta["current_source_count"] == 3
    assert meta["detail_attempts"] == 0


def test_retry_is_bounded_and_failure_is_closed() -> None:
    failed_url = gwangyang.gwangyang_list_url("resident_golyak", 1)
    site = FixtureSite(always_fail_url=failed_url)
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert site.calls[failed_url] == gwangyang.GWANGYANG_FETCH_ATTEMPTS
    assert "persistent fixture timeout" in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_wrong_target_and_invalid_limits_fail_without_fetching() -> None:
    site = FixtureSite()
    rows, _parser, meta = gwangyang.collect(
        replace(Target(), provider="WRONG"),
        session_factory=DummySession,
        fetcher=site.fetch,
    )
    assert rows == []
    assert not site.calls
    assert "canonical Gwangyang education owner" in meta[
        "configured_collection_error"
    ]

    rows, _parser, meta = gwangyang.collect(
        Target(),
        max_workers=0,
        session_factory=DummySession,
        fetcher=site.fetch,
    )
    assert rows == []
    assert not site.calls
    assert meta["source_cap_reached"] is True
    assert "invalid collection limits" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("GWANGYANG_LIVE_TEST") != "1",
    reason="set GWANGYANG_LIVE_TEST=1 for the 300-request live contract audit",
)
def test_live_complete_audit_matches_2026_07_21_discovery() -> None:
    rows, _parser, meta = gwangyang.collect(
        Target(),
        today="2026-07-21",
        max_pages=132,
        detail_limit=157,
        max_workers=4,
    )
    assert meta["configured_collection_error"] == ""
    assert meta["source_totals"] == {
        "women_culture": 402,
        "citizen_it": 60,
        "digital_learning": 83,
        "resident_okgok": 8,
        "resident_golyak": 108,
        "resident_jungma": 198,
        "resident_taein": 0,
        "resident_geumho": 34,
        "resident_gwangyeong": 39,
        "resident_gwangyang_eup": 47,
    }
    assert meta["source_rows"] == 979
    assert meta["data_pages"] == 102
    assert meta["single_education_date_count"] == 39
    assert meta["current_source_count"] == len(rows) == 157
    assert meta["status_counts"] == {"CLOSED": 140, "OPEN": 11, "SCHEDULED": 6}
    assert meta["application_control_count"] == 11
    assert meta["snapshot_complete"] is True
