from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html import escape
import inspect
import os
import ssl
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_incheon_ongjin as ongjin


@dataclass
class _Response:
    url: str
    html: str
    status_code: int = 200

    @property
    def content(self) -> bytes:
        return self.html.encode("utf-8")


class _Session:
    def __init__(self) -> None:
        self.closed = False
        self.list_seen = 0

    def close(self) -> None:
        self.closed = True


def _target(
    provider: str = ongjin.ONGJIN_PROVIDER,
    url: str = ongjin.ONGJIN_CANONICAL_URL,
    candidate_id: str = ongjin.ONGJIN_CANONICAL_CANDIDATE_ID,
) -> dict[str, str]:
    return {
        "provider": provider,
        "candidate_id": candidate_id,
        "url": url,
        "name": "옹진군 주민교육프로그램",
        "branch": ongjin.ONGJIN_MUNICIPALITY_NAME,
    }


def _course(
    identity: str,
    *,
    title: str | None = None,
    branch: str = "영흥면",
    reception: str = "접수마감",
    course_status: str = "교육중",
    start: str = "2026-03-03",
    end: str = "2026-12-31",
    schedule: str = "화,금 19:30 ~ 21:30",
    category: str = "취미",
    target: str = "옹진군민",
    fee: str = "무료",
    material_fee: str = "없음",
    venue: str = "옹진국민체육센터 2층",
    address: str = "인천광역시 옹진군 영흥면",
) -> dict[str, str]:
    return {
        "identity": identity,
        "title": title or f"옹진 안전교육 {identity}",
        "branch": branch,
        "reception": reception,
        "course_status": course_status,
        "start": start,
        "end": end,
        "schedule": schedule,
        "category": category,
        "target": target,
        "fee": fee,
        "material_fee": material_fee,
        "venue": venue,
        "address": address,
    }


def _historical(identity: str) -> dict[str, str]:
    return _course(
        identity,
        branch="백령면",
        course_status="교육종료",
        start="2025-03-04",
        end="2025-12-31",
        schedule="수요일 10:00 ~ 12:00",
        venue="공공도서관 1층",
        address="인천광역시 옹진군 백령면",
    )


def _default_courses() -> list[dict[str, str]]:
    return [
        _course("100", title="시니어 건강체조"),
        _course("99", title="도예교실", schedule="수요일 14:00 ~ 16:00"),
        _course(
            "98",
            title="미싱클래스",
            schedule="월요일 16:00 ~ 210:00",
        ),
        *[_historical(str(identity)) for identity in range(201, 209)],
    ]


def _registry_options(values: Mapping[str, str]) -> str:
    return '<option value="">전체</option>' + "".join(
        f'<option value="{escape(code, quote=True)}">{escape(name)}</option>'
        for code, name in values.items()
    )


def _list_row(course: Mapping[str, str]) -> str:
    detail_url = ongjin.ongjin_detail_url(course["identity"])
    return f"""
      <tr>
        <td><a href="{escape(detail_url, quote=True)}">{escape(course['title'])}</a></td>
        <td>{escape(course['branch'])}</td>
        <td><p>교육기간 : {course['start']} ~ {course['end']}</p></td>
        <td><span class="lectag">{escape(course['reception'])}</span>
          <span class="lectag">{escape(course['course_status'])}</span></td>
      </tr>
    """


def _list_page(
    courses: list[dict[str, str]],
    *,
    reported_page: int,
    total: int,
    last: int,
) -> str:
    selected_url = ongjin.ongjin_list_url(reported_page)
    last_url = ongjin.ongjin_list_url(last)
    return f"""
      <html><head><title>{ongjin._LIST_TITLE}</title></head><body>
        <div id="contents">
          <form action="{ongjin.ONGJIN_LIST_PATH}" method="get">
            <input type="hidden" name="sitediv" value="main">
            <select id="instcd0" name="instcd0">
              {_registry_options(ongjin.ONGJIN_INSTITUTIONS)}
            </select>
            <select id="leccate" name="leccate">
              {_registry_options(ongjin.ONGJIN_CATEGORIES)}
            </select>
          </form>
          <p class="right">전체 강좌 수 : {total}</p>
          <div class="paging">
            <a class="select" href="{escape(selected_url, quote=True)}"
               title="{reported_page} page(현재 페이지)">{reported_page}</a>
            <a class="last" href="{escape(last_url, quote=True)}">마지막</a>
          </div>
          <table class="general_board"><thead><tr>
            {''.join(f'<th>{header}</th>' for header in ongjin._LIST_HEADERS)}
          </tr></thead><tbody>
            {''.join(_list_row(course) for course in courses)}
          </tbody></table>
        </div>
      </body></html>
    """


def _detail_page(
    course: Mapping[str, str],
    *,
    title: str | None = None,
    branch: str | None = None,
    education_period: str | None = None,
    schedule: str | None = None,
    omit_label: str = "",
    application_control: bool = False,
) -> str:
    pairs = [
        ("접수상태", course["reception"]),
        ("강좌상태", course["course_status"]),
        ("교육기관", branch or course["branch"]),
        ("분야", course["category"]),
        ("대상", course["target"]),
        (
            "교육기간",
            education_period or f"{course['start']} ~ {course['end']}",
        ),
        ("교육요일시간", schedule or course["schedule"]),
        ("수강료", course["fee"]),
        ("기타/재료비", course["material_fee"]),
        ("교육내용", "SECRET DESCRIPTION private@example.test"),
        ("교육상세내용", "SECRET LONG FREE TEXT 12345"),
        ("입금정보", "SECRET BANK ACCOUNT 999-999"),
        ("취소환불규정", "SECRET REFUND POLICY"),
        ("첨부파일", "SECRET attachment-private.pdf"),
        ("교육장명", course["venue"]),
        ("교육장주소", course["address"]),
        ("문의전화", "SECRET PHONE 010-1111-2222"),
    ]
    pairs = [pair for pair in pairs if pair[0] != omit_label]
    control = '<a href="/apply.do">수강신청</a>' if application_control else ""
    return f"""
      <html><head><title>{ongjin._DETAIL_TITLE}</title></head><body>
        <div id="detail_con"><div class="board_view">
          <p class="title">{escape(title or course['title'])}</p>
          <ul class="datalist"><li>
            {''.join(f'<dl><dt>{key}</dt><dd>{escape(value)}</dd></dl>' for key, value in pairs)}
          </li></ul>
          {control}
        </div></div>
      </body></html>
    """


def _fetcher(
    courses: list[dict[str, str]],
    *,
    calls: list[str] | None = None,
    sentinel_drift: bool = False,
    boundary_drift: bool = False,
    total_drift: bool = False,
    detail_overrides: Mapping[str, str] | None = None,
):
    pages = [
        courses[index : index + ongjin.ONGJIN_PAGE_SIZE]
        for index in range(0, len(courses), ongjin.ONGJIN_PAGE_SIZE)
    ]
    total = len(courses)
    last = len(pages)
    page_calls: dict[int, int] = {}
    details = {
        course["identity"]: _detail_page(course)
        for course in courses
    }
    details.update(detail_overrides or {})

    def fetch(session: _Session, url: str, timeout: int) -> _Response:
        assert timeout == 7
        if calls is not None:
            calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == ongjin.ONGJIN_DETAIL_PATH:
            assert session.list_seen > 0, "details require the list session cookie"
            identity = query["lecseq"][0]
            return _Response(url, details[identity])
        assert parsed.path == ongjin.ONGJIN_LIST_PATH
        session.list_seen += 1
        requested = int(query["nowPage"][0])
        reported = min(requested, last)
        page_calls[requested] = page_calls.get(requested, 0) + 1
        page_courses = pages[reported - 1]
        if sentinel_drift and requested == last + 1:
            page_courses = pages[0]
        if boundary_drift and requested == 1 and page_calls[requested] > 1:
            changed = {**page_courses[0], "title": "경계에서 변경된 강좌"}
            page_courses = [changed, *page_courses[1:]]
        page_total = total + int(total_drift and requested == 2)
        return _Response(
            url,
            _list_page(
                page_courses,
                reported_page=reported,
                total=page_total,
                last=last,
            ),
        )

    return fetch


def _collect(
    courses: list[dict[str, str]] | None = None,
    *,
    fetcher=None,
    session_factory=_Session,
    **kwargs: Any,
):
    courses = _default_courses() if courses is None else courses
    return ongjin.collect(
        _target(),
        timeout=7,
        max_pages=kwargs.pop("max_pages", 10),
        detail_limit=kwargs.pop("detail_limit", 20),
        max_requests=kwargs.pop("max_requests", 30),
        today=kwargs.pop("today", "2026-07-22"),
        fetcher=fetcher or _fetcher(courses),
        session_factory=session_factory,
        sleeper=lambda _: None,
        **kwargs,
    )


def test_constants_target_urls_owner_boundaries_and_registries_are_exact() -> None:
    assert ongjin.ONGJIN_PROVIDER == "MUNI_WWW_ONGJIN_GO_KR_0243B215"
    assert ongjin.ONGJIN_DUPLICATE_PROVIDER == "MUNI_WWW_ONGJIN_GO_KR_9B7F8E38"
    assert ongjin.ONGJIN_CANONICAL_CANDIDATE_ID == "MUNI_IR_66B91AC3F17B"
    assert ongjin.ONGJIN_LEDGER_CANDIDATE_ID == "MUNI_IR_8D6870866D4E"
    assert ongjin.ONGJIN_MUNICIPALITY_CODE == "2872000000"
    assert ongjin.ONGJIN_CANONICAL_URL == (
        "https://www.ongjin.go.kr/open_content/main/community/education/program.jsp"
    )
    assert ongjin.ONGJIN_LEDGER_URL == (
        "https://www.ongjin.go.kr/open_content/main/lecture/lectureList.do?sitediv=main"
    )
    assert ongjin.is_target(_target())
    assert ongjin.is_target(
        _target(
            url=ongjin.ONGJIN_LEDGER_URL,
            candidate_id=ongjin.ONGJIN_LEDGER_CANDIDATE_ID,
        )
    )
    assert not ongjin.is_target(_target(provider="OTHER"))
    assert not ongjin.is_target(_target(candidate_id="MUNI_IR_WRONG"))
    assert not ongjin.is_target(_target(url="https://www.ongjin.go.kr/other"))
    assert ongjin.ONGJIN_INSTITUTIONS == {
        "main": "옹진군청",
        "bukdo": "북도면",
        "yeonpyeong": "연평면",
        "baekryeong": "백령면",
        "daecheong": "대청면",
        "deokjeok": "덕적면",
        "jawol": "자월면",
        "yeongheung": "영흥면",
    }
    audit = ongjin.ONGJIN_OWNER_BOUNDARY_AUDIT
    assert audit[ongjin.ONGJIN_DUPLICATE_PROVIDER]["duplicate_of"] == (
        ongjin.ONGJIN_PROVIDER
    )
    assert audit["MUNI_IR_5E84D62B0E43"]["decision"] == (
        "wrong_category_statistics_page"
    )
    assert audit["MUNI_IR_61DCB5D8E152"]["decision"] == (
        "cyber_learning_information_page_not_course_ledger"
    )
    assert audit["MUNI_IR_6FC6F8469CA1"]["owner"] == "INCHEON_RESERVATION"


def test_url_helpers_are_strictly_scoped_and_identity_bound() -> None:
    parsed = urlparse(ongjin.ongjin_list_url(7))
    assert parsed.hostname == ongjin.ONGJIN_HOST
    assert parse_qs(parsed.query) == {"sitediv": ["main"], "nowPage": ["7"]}
    current = ongjin.ongjin_list_url(7)
    assert ongjin.canonical_ongjin_detail_identity(
        current,
        "/open_content/main/lecture/lectureDetail.do?lecseq=123&sitediv=main",
    ) == "123"
    assert not ongjin.canonical_ongjin_detail_identity(
        current,
        "https://evil.example/open_content/main/lecture/lectureDetail.do?lecseq=123&sitediv=main",
    )
    assert not ongjin.canonical_ongjin_detail_identity(
        current,
        "/open_content/main/lecture/lectureDetail.do?lecseq=123&sitediv=dong",
    )
    assert not ongjin.canonical_ongjin_detail_identity(
        current,
        "/open_content/main/lecture/lectureDetail.do?lecseq=123&sitediv=main&extra=1",
    )
    with pytest.raises(ongjin.OngjinContractError):
        ongjin.ongjin_detail_url("../123")


def test_tls_adapter_is_scoped_strict_and_pinned_to_audited_intermediate() -> None:
    der = ssl.PEM_cert_to_DER_cert(ongjin.ONGJIN_AIA_INTERMEDIATE_PEM)
    assert sha256(der).hexdigest() == ongjin.ONGJIN_AIA_INTERMEDIATE_SHA256
    assert ongjin.ONGJIN_AIA_INTERMEDIATE_SHA256 == (
        "a6f9c967eb8aa9283a1ca649b87b764720e9f5c3afa81c150676f4ca36e98cf6"
    )
    assert ongjin.ONGJIN_LEAF_SHA256_AUDITED_2026_07_22 == (
        "6475b581d35c2d1b6fc90b915097451af084428331daba9dbfe0227fddac0ccc"
    )
    session = ongjin.ongjin_session_factory()
    try:
        adapter = session.get_adapter("https://www.ongjin.go.kr/example")
        assert isinstance(adapter, ongjin._OngjinSSLContextAdapter)
        assert adapter._context.verify_mode == ssl.CERT_REQUIRED
        assert adapter._context.check_hostname is True
        assert not isinstance(
            session.get_adapter("https://example.com/example"),
            ongjin._OngjinSSLContextAdapter,
        )
    finally:
        session.close()
    source = inspect.getsource(ongjin)
    assert "verify" + "=False" not in source
    assert "CERT" + "_NONE" not in source
    assert "check_hostname" + " = False" not in source


def test_complete_archive_clamp_boundaries_current_details_and_private_values() -> None:
    calls: list[str] = []
    sessions: list[_Session] = []

    def factory() -> _Session:
        session = _Session()
        sessions.append(session)
        return session

    courses = _default_courses()
    rows, parser, meta = _collect(
        courses,
        fetcher=_fetcher(courses, calls=calls),
        session_factory=factory,
    )
    assert parser == ongjin.ONGJIN_PARSER
    assert [row["provider_course_id"] for row in rows] == [
        f"{ongjin.ONGJIN_PROVIDER}:lecture:100",
        f"{ongjin.ONGJIN_PROVIDER}:lecture:99",
        f"{ongjin.ONGJIN_PROVIDER}:lecture:98",
    ]
    assert len(sessions) == 1
    assert sessions[0].closed is True
    assert sessions[0].list_seen == 5
    assert all(row["branch"] == "영흥면" for row in rows)
    assert all(row["status"] == "CLOSED" for row in rows)
    assert all(row["application_type"] == "INFORMATION_ONLY" for row in rows)
    assert all(row["reservation_available"] is False for row in rows)
    assert all("application_url" not in row for row in rows)
    anomaly = next(row for row in rows if row["provider_course_id"].endswith(":98"))
    assert anomaly["title"] == "미싱클래스"
    assert anomaly["schedule_raw"] == "월요일 16:00 ~ 210:00"
    assert anomaly["raw_fields"]["audited_schedule_anomaly"] is True
    assert meta["source_total"] == 11
    assert meta["pages"] == 2
    assert meta["page_sizes"] == (10, 1)
    assert meta["list_requests"] == 5
    assert meta["sentinel_requests"] == 1
    assert meta["sentinel_page"] == 3
    assert meta["sentinel_kind"] == "exact_final_page_clamp"
    assert meta["stability_rechecks"] == 2
    assert meta["current_source_count"] == 3
    assert meta["detail_pages"] == 3
    assert meta["returned_count"] == 3
    assert meta["network_requests"] == 8 == len(calls)
    assert meta["source_branch_counts"] == {"영흥면": 3, "백령면": 8}
    assert meta["branch_counts"] == {"영흥면": 3}
    assert meta["private_detail_values_read"] == 0
    assert meta["safe_detail_field_count"] == 11
    assert meta["private_detail_field_count"] == 6
    assert meta["application_control_count"] == 0
    assert meta["audited_schedule_anomaly_count"] == 1
    assert meta["semantic_duplicate_count"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    result_blob = repr((rows, meta))
    for secret in (
        "SECRET DESCRIPTION",
        "private@example.test",
        "SECRET LONG FREE TEXT",
        "SECRET BANK ACCOUNT",
        "SECRET REFUND POLICY",
        "attachment-private.pdf",
        "010-1111-2222",
    ):
        assert secret not in result_blob


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_pages": 2}, "sentinel page 3 exceeds max_pages cap"),
        ({"detail_limit": 2}, "detail_limit cap allows 2 of 3"),
        ({"max_requests": 7}, "max_requests cap allows 7 of 8"),
    ],
)
def test_all_caps_fail_closed(kwargs: Mapping[str, int], message: str) -> None:
    rows, _, meta = _collect(**kwargs)
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False


def test_wrong_target_returns_configured_error_without_network() -> None:
    rows, parser, meta = ongjin.collect(
        _target(provider="OTHER"),
        fetcher=lambda *_: pytest.fail("wrong target must not touch network"),
    )
    assert rows == []
    assert parser == ongjin.ONGJIN_PARSER
    assert "does not match" in meta["configured_collection_error"]
    assert "network_requests" not in meta


@pytest.mark.parametrize(
    ("fetcher_options", "message"),
    [
        ({"sentinel_drift": True}, "final-page clamp contents changed"),
        ({"boundary_drift": True}, "first page changed during stable recheck"),
        ({"total_drift": True}, "advertised source total changed"),
    ],
)
def test_census_sentinel_and_boundary_drift_fail_closed(
    fetcher_options: Mapping[str, bool],
    message: str,
) -> None:
    courses = _default_courses()
    rows, _, meta = _collect(
        courses,
        fetcher=_fetcher(courses, **fetcher_options),
    )
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_duplicate_identity_generic_test_and_unknown_institution_fail_closed() -> None:
    duplicate = _default_courses()
    duplicate[1] = {**duplicate[1], "identity": duplicate[0]["identity"]}
    rows, _, meta = _collect(duplicate)
    assert rows == []
    assert "duplicate lecture identities" in meta["configured_collection_error"]

    for title in ("테스트", "교육 안내", "sample-2"):
        courses = _default_courses()
        courses[0] = {**courses[0], "title": title}
        rows, _, meta = _collect(courses)
        assert rows == []
        assert "unaudited test/information row" in meta["configured_collection_error"]

    courses = _default_courses()
    courses[0] = {**courses[0], "branch": "임의 교육기관"}
    rows, _, meta = _collect(courses)
    assert rows == []
    assert "unknown official institution" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"title": "다른 강좌"}, "detail title does not match"),
        ({"branch": "백령면"}, "detail institution does not match"),
        (
            {"education_period": "2026-03-03 ~ 2026-11-30"},
            "detail education period disagrees",
        ),
        ({"omit_label": "대상"}, "detail field schema changed"),
        ({"application_control": True}, "unaudited application control"),
    ],
)
def test_detail_identity_schema_period_and_application_controls_fail_closed(
    override: Mapping[str, Any],
    message: str,
) -> None:
    courses = _default_courses()
    detail_overrides = {"100": _detail_page(courses[0], **override)}
    rows, _, meta = _collect(
        courses,
        fetcher=_fetcher(courses, detail_overrides=detail_overrides),
    )
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["details_complete"] is False


def test_invalid_schedule_is_allowed_only_for_exact_audited_identity_tuple() -> None:
    courses = _default_courses()
    assert _collect(courses)[2]["configured_collection_error"] == ""

    detail_overrides = {
        "100": _detail_page(courses[0], schedule="화요일 16:00 ~ 210:00")
    }
    rows, _, meta = _collect(
        courses,
        fetcher=_fetcher(courses, detail_overrides=detail_overrides),
    )
    assert rows == []
    assert "unaudited invalid schedule clock for 100" in meta[
        "configured_collection_error"
    ]

    changed = _default_courses()
    changed[2] = {**changed[2], "title": "미싱클래스 변경"}
    rows, _, meta = _collect(changed)
    assert rows == []
    assert "unaudited invalid schedule clock for 98" in meta[
        "configured_collection_error"
    ]


def test_semantic_duplicates_fail_closed() -> None:
    courses = _default_courses()
    courses[1] = {
        **courses[1],
        "title": courses[0]["title"],
        "branch": courses[0]["branch"],
        "start": courses[0]["start"],
        "end": courses[0]["end"],
        "schedule": courses[0]["schedule"],
    }
    rows, _, meta = _collect(courses)
    assert rows == []
    assert "semantic duplicates" in meta["configured_collection_error"]


def test_no_current_data_is_an_explicit_complete_snapshot() -> None:
    courses = [_historical(str(identity)) for identity in range(301, 312)]
    rows, _, meta = _collect(courses)
    assert rows == []
    assert meta["source_total"] == 11
    assert meta["current_source_count"] == 0
    assert meta["detail_pages"] == 0
    assert meta["network_requests"] == 5
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert "complete Ongjin archive" in meta["no_current_reason"]
    assert meta["configured_collection_error"] == ""


@pytest.mark.skipif(
    os.getenv("RUN_INCHEON_ONGJIN_LIVE_TEST") != "1",
    reason="set RUN_INCHEON_ONGJIN_LIVE_TEST=1 for exact Ongjin live audit",
)
def test_live_exact_complete_archive_current_details_names_and_privacy() -> None:
    rows, parser, meta = ongjin.collect(
        _target(),
        timeout=30,
        max_pages=10,
        detail_limit=20,
        max_requests=30,
        today="2026-07-22",
    )
    assert parser == ongjin.ONGJIN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_total"] == 31
    assert meta["pages"] == 4
    assert meta["page_sizes"] == (10, 10, 10, 1)
    assert meta["list_requests"] == 7
    assert meta["sentinel_page"] == 5
    assert meta["sentinel_kind"] == "exact_final_page_clamp"
    assert meta["stability_rechecks"] == 2
    assert meta["current_source_count"] == 12
    assert meta["detail_pages"] == 12
    assert meta["returned_count"] == 12 == len(rows)
    assert meta["network_requests"] == 19
    assert meta["network_retry_count"] == 0
    assert meta["current_ids"] == (
        "100",
        "99",
        "98",
        "96",
        "95",
        "94",
        "60",
        "48",
        "46",
        "44",
        "43",
        "42",
    )
    assert meta["source_branch_counts"] == {"영흥면": 15, "백령면": 16}
    assert meta["branch_counts"] == {"영흥면": 12}
    assert meta["branch_count"] == 1
    assert meta["source_status_counts"] == {
        "접수마감 교육중": 12,
        "접수마감 교육종료": 19,
    }
    assert meta["current_source_status_counts"] == {"접수마감 교육중": 12}
    assert meta["status_counts"] == {"CLOSED": 12}
    assert meta["institution_registry_count"] == 8
    assert meta["detail_field_count"] == 17
    assert meta["safe_detail_field_count"] == 11
    assert meta["private_detail_field_count"] == 6
    assert meta["private_detail_values_read"] == 0
    assert meta["application_control_count"] == 0
    assert meta["audited_schedule_anomaly_count"] == 1
    assert meta["semantic_duplicate_count"] == 0
    assert meta["snapshot_complete"] is True
    assert [row["title"] for row in rows] == [
        "시니어 건강체조",
        "도예교실",
        "미싱클래스",
        "캘리그라피",
        "라인댄스",
        "합창단",
        "댄스스포츠",
        "노래교실",
        "통기타",
        "풍물놀이",
        "꽃꽂이",
        "색소폰",
    ]
    assert all(row["branch"] == "영흥면" for row in rows)
    assert all(row["status"] == "CLOSED" for row in rows)
    assert all(row["end_date"] >= "2026-07-22" for row in rows)
    assert len({row["provider_course_id"] for row in rows}) == 12
    anomaly = next(row for row in rows if row["provider_course_id"].endswith(":98"))
    assert anomaly["schedule_raw"] == "월요일 16:00 ~ 210:00"
    result_blob = repr((rows, meta))
    assert "032-899-3817" not in result_blob
    assert "시니어 건강체조 교육" not in result_blob
