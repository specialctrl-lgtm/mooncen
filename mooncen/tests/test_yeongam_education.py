from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
import hashlib
import inspect
import os
import re
import ssl
from urllib.parse import parse_qs, urlparse

from bs4.element import Tag
import pytest
import requests

from Crawler import municipal_yeongam as yeongam


@dataclass
class Target:
    provider: str = yeongam.YEONGAM_PROVIDER
    url: str = yeongam.YEONGAM_CANONICAL_URL


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    venue: str
    current: int
    capacity: int
    source_status: str
    apply_start: str
    apply_end: str
    start: str
    end: str
    target: str = "관내 초등학생 전학년"
    education_time: str = "10:00 ~ 12:00"
    education_day: str = "월~금"


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class DummyResponse:
    def __init__(self, url: str, body: str, status_code: int = 200) -> None:
        self.url = url
        self.status_code = status_code
        self.content = body.encode("utf-8")
        self.headers = {"Content-Type": "text/html; charset=utf-8"}


def _courses() -> list[Course]:
    result: list[Course] = []
    current = {
        35: Course(
            "35", "2026년 학산도서관 여름방학특강", "학산도서관 1층 독서토론방",
            6, 6, "신청하기", "2026-07-13", "2026-07-27",
            "2026-08-05", "2026-08-07",
        ),
        34: Course(
            "34", "2026 학산도서관 여름독서교실", "학산도서관 1층 독서토론방",
            6, 6, "신청하기", "2026-07-13", "2026-07-27",
            "2026-08-10", "2026-08-14", education_day="월요일~금요일 (5회)",
        ),
        33: Course(
            "33", "삼호도서관 여름특강 [창의력 쑥쑥! 여름 공예]", "삼호도서관 문화강좌실",
            0, 15, "접수대기", "2026-07-22", "2026-08-04",
            "2026-08-04", "2026-08-06", target="관내 초등학교 1~6학년",
            education_time="14:00 ~ 16:00", education_day="화~목",
        ),
        32: Course(
            "32", "삼호도서관 여름독서교실", "삼호도서관 문화강좌실",
            7, 12, "신청하기", "2026-07-15", "2026-08-03",
            "2026-08-03", "2026-08-07", target="관내 초등학교 3~6학년",
        ),
    }
    for identity in range(35, 0, -1):
        if identity in current:
            result.append(current[identity])
            continue
        if identity % 3 == 0:
            venue = "학산도서관 독서토론방"
        elif identity % 3 == 1:
            venue = "영암도서관 2층 문화강좌실"
        else:
            venue = "삼호도서관 시청각교육실"
        result.append(
            Course(
                str(identity), f"영암군립도서관 과거 강좌 {identity}", venue,
                10, 10, "신청마감", "2025-01-01", "2025-01-10",
                "2025-02-01", "2025-02-28",
            )
        )
    return result


def _detail_href(course: Course, page: int) -> str:
    return (
        f"/home/newlib/culture/culture_02/show/{course.identity}"
        f"?page={page}&search=&keyword="
    )


def _pager(page: int, last: int, *, sentinel: bool = False) -> str:
    values: list[str] = []
    for number in range(1, last + 1):
        if not sentinel and number == page:
            values.append(f"<strong>{number}</strong>")
        else:
            values.append(
                f'<a href="?page={number}&amp;search=&amp;keyword=" '
                f'title="{number} 페이지">{number}</a>'
            )
    return '<div class="pagenum">' + "".join(values) + "</div>"


def _list_row(course: Course, page: int) -> str:
    href = _detail_href(course, page).replace("&", "&amp;")
    if course.source_status == "신청하기":
        state_image = "lecture_application_ing.gif"
        detail_control = f'<a href="{href}"></a>'
    elif course.source_status == "접수대기":
        state_image = "lecture_application_wait.gif"
        detail_control = ""
    else:
        state_image = "lecture_application_end.gif"
        detail_control = ""
    education_state = "교육준비" if course.end >= "2026-07-21" else "교육종료"
    education_image = (
        "lecture_edu_aff.gif" if education_state == "교육준비" else "lecture_edu_end.gif"
    )
    return f"""
      <tr>
        <td><a href="{href}">{escape(course.title)}</a></td>
        <td>{escape(course.venue)}</td>
        <td>{course.current}/{course.capacity}</td>
        <td>
          <img src="/images/youth/sub/lecture_application.gif" alt="신청">
          <img src="/images/youth/sub/{state_image}" alt="{course.source_status}">
          {course.apply_start}~{course.apply_end}
          {detail_control}
          <img src="/images/youth/sub/lecture_edu.gif" alt="교육">
          <img src="/images/youth/sub/{education_image}" alt="{education_state}">
          {course.start}~{course.end}
        </td>
      </tr>
    """


def _list_html(
    courses: list[Course],
    page: int,
    *,
    last: int = 3,
    sentinel: bool = False,
) -> str:
    if sentinel:
        body = '<tr><td colspan="5">검색내역이 없습니다.</td></tr>'
    else:
        start = (page - 1) * yeongam.YEONGAM_PAGE_SIZE
        body = "".join(
            _list_row(course, page)
            for course in courses[start : start + yeongam.YEONGAM_PAGE_SIZE]
        )
    return f"""<!doctype html><html><head>
      <title>강좌 신청  &lt;  문화행사   &lt; 영암군</title></head><body>
      <table id="board_list_table" class="list_table">
        <caption>교육명, 접수인원, 기간으로 구성된 표</caption>
        <thead><tr><th>교육명</th><th>교육장소</th><th>접수인원</th><th>기간</th></tr></thead>
        <tbody>{body}</tbody>
      </table>{_pager(page, last, sentinel=sentinel)}</body></html>"""


def _detail_row(label: str, value: str, *, private: bool = False) -> str:
    marker = ' data-private="true"' if private else ""
    return f"<tr><th>{escape(label)}</th><td{marker}>{escape(value)}</td></tr>"


def _detail_html(
    course: Course,
    *,
    identity_bound: bool = False,
    bad_gate: bool = False,
    venue_override: str = "",
    missing_comment: bool = False,
    unknown_field: bool = False,
    full_gained_control: bool = False,
) -> str:
    control = ""
    if course.source_status == "신청하기" and course.current < course.capacity:
        if identity_bound:
            returned = (
                f"//{yeongam.YEONGAM_HOST}{yeongam.YEONGAM_DETAIL_PREFIX}"
                f"{course.identity}"
            )
        elif bad_gate:
            returned = f"//{yeongam.YEONGAM_HOST}{yeongam.YEONGAM_LIST_PATH}?course=999"
        else:
            returned = f"//{yeongam.YEONGAM_HOST}{yeongam.YEONGAM_LIST_PATH}"
        href = (
            f"/home/newlib/support/login?set=attest&amp;return_url={returned}"
        )
        control = (
            f'<a class="btn_submit next" href="{href}">본인 확인 후 신청하기</a>'
        )
    elif full_gained_control:
        returned = (
            f"//{yeongam.YEONGAM_HOST}{yeongam.YEONGAM_DETAIL_PREFIX}{course.identity}"
        )
        control = (
            '<a class="btn_submit next" '
            f'href="/home/newlib/support/login?set=attest&amp;return_url={returned}">'
            "본인 확인 후 신청하기</a>"
        )
    rows = "".join(
        (
            _detail_row("프로그램명", course.title),
            _detail_row(
                "접수기간", f"{course.apply_start} 09:00 ~ {course.apply_end} 18:00"
            ),
            _detail_row("모집인원", f"{course.capacity}명"),
            _detail_row("모집대상", course.target),
            _detail_row("교육기간", f"{course.start} ~ {course.end}"),
            _detail_row("교육시간", course.education_time),
            _detail_row("교육요일", course.education_day),
            _detail_row("교육장소", venue_override or course.venue),
            _detail_row("강사", "PRIVATE_INSTRUCTOR instructor@example.com", private=True),
            _detail_row("문의전화", "010-1234-5678", private=True),
            _detail_row("수업내용", "PRIVATE_LESSON_CONTENT", private=True),
            _detail_row("첨부파일", "private-applicants.xlsx", private=True),
            (_detail_row("새로운 개인정보", "PRIVATE_UNKNOWN", private=True) if unknown_field else ""),
        )
    )
    comment = "" if missing_comment else f"""
      <div class="comment" data-private="true">
        <form id="comment_form" name="comment_form" method="post"
          action="/home/newlib/culture/culture_02/show/{course.identity}?sub_mode=comment_write">
          <input name="writer" value="PRIVATE_APPLICANT">
        </form>
        <div id="comment_list" class="comment_list" data-private="true">
          PRIVATE_COMMENT 010-9876-5432
        </div>
      </div>
    """
    return f"""<!doctype html><html><head>
      <title>강좌 신청 &lt; 문화행사 &lt; 영암군</title></head><body>
      <form id="searchform" method="post" action="/home/www/support/search"></form>
      <table class="edu_form res_th"><tbody>{rows}</tbody></table>
      {control}<a class="list_btn" href="/home/newlib/culture/culture_02">목록</a>
      {comment}
      </body></html>"""


def _fixture(
    *,
    bad_sentinel: bool = False,
    drift_page: int = 0,
    duplicate_identity: bool = False,
    detail_venue_mismatch: bool = False,
    identity_bound: bool = False,
    bad_gate: bool = False,
    missing_comment: bool = False,
    unknown_field: bool = False,
    full_gained_control: bool = False,
    transient_url: str = "",
):
    courses = _courses()
    if duplicate_identity:
        courses[15] = replace(courses[15], identity=courses[0].identity)
    sessions: list[DummySession] = []
    calls: list[str] = []
    counts: dict[str, int] = {}

    def session_factory() -> DummySession:
        session = DummySession()
        sessions.append(session)
        return session

    def fetch(_session: DummySession, url: str, timeout: int) -> DummyResponse:
        assert timeout > 0
        calls.append(url)
        counts[url] = counts.get(url, 0) + 1
        if transient_url == url and counts[url] == 1:
            raise requests.ConnectionError("transient fixture failure")
        parsed = urlparse(url)
        if parsed.path == yeongam.YEONGAM_LIST_PATH:
            page = int(parse_qs(parsed.query).get("page", ["1"])[0])
            if page == 4:
                if bad_sentinel:
                    body = _list_html(courses, 3)
                else:
                    body = _list_html(courses, page, sentinel=True)
                return DummyResponse(url, body)
            values = courses
            if drift_page == page and counts[url] > 1:
                values = list(courses)
                offset = (page - 1) * yeongam.YEONGAM_PAGE_SIZE
                values[offset] = replace(values[offset], title=values[offset].title + " 변경")
            return DummyResponse(url, _list_html(values, page))
        match = re.fullmatch(
            rf"{re.escape(yeongam.YEONGAM_DETAIL_PREFIX)}(?P<identity>\d+)",
            parsed.path,
        )
        assert match, f"collector crossed a read-only course boundary: {url}"
        identity = match.group("identity")
        course = next(value for value in courses if value.identity == identity)
        body = _detail_html(
            course,
            identity_bound=identity_bound and identity == "32",
            bad_gate=bad_gate and identity == "32",
            venue_override=("영암도서관 잘못된 장소" if detail_venue_mismatch and identity == "32" else ""),
            missing_comment=missing_comment and identity == "32",
            unknown_field=unknown_field and identity == "32",
            full_gained_control=full_gained_control and identity == "35",
        )
        return DummyResponse(url, body)

    return fetch, session_factory, sessions, calls


def _collect(**fixture_options):
    fetch, session_factory, sessions, calls = _fixture(**fixture_options)
    rows, parser, meta = yeongam.collect_yeongam_education(
        Target(),
        timeout=7,
        max_pages=20,
        detail_limit=20,
        today="2026-07-21",
        fetcher=fetch,
        session_factory=session_factory,
        sleeper=lambda _seconds: None,
    )
    return rows, parser, meta, sessions, calls


def test_collects_every_page_sentinel_boundaries_and_safe_current_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Tag.get_text

    def guarded(self: Tag, *args, **kwargs):
        if self.get("data-private") == "true":
            raise AssertionError("private detail/comment boundary was read")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Tag, "get_text", guarded)
    rows, parser, meta, sessions, calls = _collect()

    assert parser == yeongam.YEONGAM_PARSER
    assert [row["provider_course_id"] for row in rows] == ["35", "34", "33", "32"]
    assert meta["source_rows"] == 35
    assert meta["data_pages"] == 3
    assert meta["page_counts"] == {1: 15, 2: 15, 3: 5}
    assert meta["list_requests"] == meta["required_list_requests"] == 6
    assert meta["sentinel_requests"] == 1
    assert meta["stability_rechecks"] == 2
    assert meta["current_source_count"] == meta["detail_pages"] == 4
    assert meta["expired_count"] == 31
    assert meta["snapshot_complete"] is True
    assert meta["current_branch_counts"] == {"학산도서관": 2, "삼호도서관": 2}
    assert meta["source_status_counts"] == {"신청하기": 3, "접수대기": 1, "신청마감": 31}
    assert meta["identity_bound_application_control_count"] == 0
    assert meta["identityless_auth_gate_excluded_count"] == 1
    assert meta["full_capacity_control_suppressed_count"] == 2
    assert meta["scheduled_control_not_visible_count"] == 1
    assert all(not row["application_url"] for row in rows)
    assert all(row["reservation_available"] is False for row in rows)
    assert rows[-1]["raw_fields"]["identityless_auth_gate_excluded"] is True
    assert all(row["raw_fields"]["comment_boundary_structurally_discarded"] for row in rows)
    assert all(row["fee"] == "공식 페이지 미기재" for row in rows)
    assert all(row["raw_fields"]["fee_source_omission"] is True for row in rows)
    serialized = repr(rows)
    assert "PRIVATE_" not in serialized
    assert "010-" not in serialized
    assert "@example.com" not in serialized
    assert not any("/support/login" in url or "sub_mode=comment" in url for url in calls)
    assert all(session.closed for session in sessions)


def test_future_identity_bound_auth_control_is_the_only_application_url() -> None:
    rows, _, meta, _, calls = _collect(identity_bound=True)
    assert meta["snapshot_complete"] is True
    assert meta["identity_bound_application_control_count"] == 1
    assert meta["identityless_auth_gate_excluded_count"] == 0
    row = next(value for value in rows if value["provider_course_id"] == "32")
    assert row["reservation_available"] is True
    assert row["application_url"] == (
        "https://www.yeongam.go.kr/home/newlib/support/login?set=attest&"
        "return_url=//www.yeongam.go.kr/home/newlib/culture/culture_02/show/32"
    )
    assert row["raw_fields"]["identity_bound_application_control"] is True
    assert row["application_url"] not in calls


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"bad_gate": True}, "not identity-bound"),
        ({"full_gained_control": True}, "unavailable course gained a control"),
        ({"detail_venue_mismatch": True}, "list/detail venue differs"),
        ({"missing_comment": True}, "comment boundary changed"),
        ({"unknown_field": True}, "unknown detail field"),
    ],
)
def test_detail_identity_control_and_pii_contracts_fail_closed(
    options: dict[str, bool], message: str
) -> None:
    rows, _, meta, _, _ = _collect(**options)
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_immediate_empty_sentinel_and_both_boundary_rechecks_are_mandatory() -> None:
    rows, _, meta, _, _ = _collect(bad_sentinel=True)
    assert rows == []
    assert "empty sentinel" in meta["configured_collection_error"]

    for page, label in ((1, "first-page"), (3, "last-page")):
        rows, _, meta, _, _ = _collect(drift_page=page)
        assert rows == []
        assert label in meta["configured_collection_error"]


def test_duplicate_official_identity_fails_the_complete_snapshot() -> None:
    rows, _, meta, _, _ = _collect(duplicate_identity=True)
    assert rows == []
    assert "duplicate official identities" in meta["configured_collection_error"]


def test_caps_fail_before_partial_save_and_expired_details_are_not_opened() -> None:
    fetch, session_factory, _, calls = _fixture()
    rows, _, meta = yeongam.collect_yeongam_education(
        Target(), max_pages=5, detail_limit=20, today="2026-07-21",
        fetcher=fetch, session_factory=session_factory, sleeper=lambda _seconds: None,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]
    assert calls == [yeongam.yeongam_list_url(1)]

    rows, _, meta, _, calls = _collect()
    assert rows
    assert not any("/show/31?" in url for url in calls)

    fetch, session_factory, _, _ = _fixture()
    rows, _, meta = yeongam.collect_yeongam_education(
        Target(), max_pages=20, detail_limit=3, today="2026-07-21",
        fetcher=fetch, session_factory=session_factory, sleeper=lambda _seconds: None,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]
    assert meta["detail_pages"] == 0


def test_transient_fetch_rebuilds_session_without_skipping_a_page() -> None:
    transient = yeongam.yeongam_list_url(2)
    rows, _, meta, sessions, calls = _collect(transient_url=transient)
    assert len(rows) == 4
    assert meta["network_retry_count"] == 1
    assert meta["sessions_created"] == 2
    assert calls.count(transient) == 2
    assert all(session.closed for session in sessions)


def test_target_url_owner_boundaries_and_live_audit_evidence_are_exact() -> None:
    assert yeongam.is_yeongam_education_target(Target())
    assert not yeongam.is_yeongam_education_target(
        Target(url=yeongam.YEONGAM_REGISTERED_DETAIL_URL)
    )
    assert yeongam.is_yeongam_candidate_alias_target(
        Target(url=yeongam.YEONGAM_REGISTERED_DETAIL_URL)
    )
    assert yeongam.is_yeongam_jntle_separate_owner_target(
        Target(provider=yeongam.YEONGAM_JNTLE_PROVIDER, url=yeongam.YEONGAM_JNTLE_URL)
    )
    assert yeongam.yeongam_list_url(1) == yeongam.YEONGAM_CANONICAL_URL
    assert yeongam.yeongam_list_url(2).endswith("?page=2&search=&keyword=")
    assert yeongam.yeongam_detail_url("385", 1).endswith(
        "/show/385?page=1&search=&keyword="
    )
    with pytest.raises(yeongam.YeongamContractError):
        yeongam.yeongam_detail_url("../../login")

    audit = yeongam.YEONGAM_DISCOVERY_AUDIT
    assert audit["source_rows"] == 75
    assert audit["page_counts"] == {1: 15, 2: 15, 3: 15, 4: 15, 5: 15}
    assert audit["current_ids"] == ("385", "384", "383", "382")
    assert audit["current_branch_counts"] == {"학산도서관": 2, "삼호도서관": 2}
    assert audit["jntle_regional_audit"]["rows"] == 306
    assert audit["jntle_regional_audit"]["current_rows"] == 0
    assert audit["lifelong_static_tabs"][yeongam.YEONGAM_EDUCITY_URL]["rows"] == 36
    assert (
        yeongam.YEONGAM_OWNER_BOUNDARY_AUDIT[yeongam.YEONGAM_JNTLE_PROVIDER]["decision"]
        == "exclude_from_county_owner_keep_regional_discovery_owner"
    )


def test_verified_tls_pin_and_no_verification_bypass() -> None:
    context = yeongam.build_yeongam_tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    der = ssl.PEM_cert_to_DER_cert(yeongam.YEONGAM_SECTIGO_INTERMEDIATE_PEM)
    assert hashlib.sha256(der).hexdigest() == yeongam.YEONGAM_SECTIGO_INTERMEDIATE_SHA256
    source = inspect.getsource(yeongam)
    assert "verify=False" not in source
    assert "verify = False" not in source
    assert ".post(" not in source


def test_invalid_targets_limits_and_dedupe_fail_closed() -> None:
    rows, _, meta = yeongam.collect_yeongam_education(
        Target(url=yeongam.YEONGAM_REGISTERED_DETAIL_URL)
    )
    assert rows == []
    assert "exact canonical" in meta["configured_collection_error"]

    rows, _, meta = yeongam.collect_yeongam_education(Target(), max_pages=True)
    assert rows == []
    assert meta["source_cap_reached"] is True

    fetch, session_factory, _, _ = _fixture()
    rows, _, meta = yeongam.collect_yeongam_education(
        Target(), max_pages=20, detail_limit=20, today="2026-07-21",
        fetcher=fetch, session_factory=session_factory, sleeper=lambda _seconds: None,
        dedupe_rows=lambda values: values[:-1],
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.environ.get("MOONCEN_RUN_YEONGAM_LIVE") != "1",
    reason="set MOONCEN_RUN_YEONGAM_LIVE=1 for the read-only official-site audit",
)
def test_live_yeongam_complete_snapshot() -> None:
    rows, parser, meta = yeongam.collect_yeongam_education(
        Target(), max_pages=20, detail_limit=20, today="2026-07-21"
    )
    assert parser == yeongam.YEONGAM_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["source_rows"] == 75
    assert meta["data_pages"] == 5
    assert meta["page_counts"] == {1: 15, 2: 15, 3: 15, 4: 15, 5: 15}
    assert meta["list_requests"] == 8
    assert meta["current_source_count"] == meta["detail_pages"] == len(rows) == 4
    assert meta["current_detail_ids"] == ["385", "384", "383", "382"]
    assert meta["current_branch_counts"] == {"학산도서관": 2, "삼호도서관": 2}
    assert meta["source_status_counts"] == {"신청하기": 3, "접수대기": 1, "신청마감": 71}
    assert meta["identity_bound_application_control_count"] == 0
    assert meta["identityless_auth_gate_excluded_count"] == 1
    assert all(row["municipality_code"] == "1280000000" for row in rows)
    assert all(not row["application_url"] for row in rows)
