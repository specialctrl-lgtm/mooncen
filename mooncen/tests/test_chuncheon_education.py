from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
from html import escape
import os
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_chuncheon as chuncheon


@dataclass(frozen=True)
class Gisu:
    e_type: str
    identity: str
    name: str
    apply_start: str
    apply_end: str
    start: str
    end: str
    source_status: str


@dataclass(frozen=True)
class Course:
    identity: str
    gisu_identity: str
    category: str
    title: str
    schedule: str
    target: str
    capacity: str
    source_status: str
    apply_start: str
    apply_end: str
    start: str
    end: str
    education_method: str = "오프라인"
    selection: str = "선착순"
    fee: str = "0원"


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class Response:
    def __init__(self, html: str, url: str) -> None:
        self.status_code = 200
        self.headers = {"Content-Type": "text/html;charset=UTF-8"}
        self.content = html.encode("utf-8")
        self.encoding = "utf-8"
        self.url = url


def _gisus() -> list[Gisu]:
    return [
        Gisu(
            "jung",
            "GISU_000000000000006",
            "2026년 제2기 대면교육",
            "2026-07-01",
            "2026-07-31",
            "2026-07-10",
            "2026-09-30",
            "신청중",
        ),
        Gisu(
            "jung",
            "GISU_000000000000005",
            "2025년 제1기 대면교육",
            "2025-01-01",
            "2025-01-31",
            "2025-02-01",
            "2025-06-30",
            "신청마감",
        ),
        Gisu(
            "dan",
            "GISU_000000000000004",
            "2026년 비대면교육",
            "2026-08-01",
            "2026-08-15",
            "2026-08-20",
            "2026-10-01",
            "신청대기",
        ),
        Gisu(
            "dan",
            "GISU_000000000000003",
            "2025년 비대면교육",
            "2025-03-01",
            "2025-03-31",
            "2025-04-01",
            "2025-05-31",
            "신청마감",
        ),
        Gisu(
            "etc",
            "GISU_000000000000002",
            "2026년 특화교육 2기",
            "2026-06-01",
            "2026-06-20",
            "2026-06-10",
            "2026-10-31",
            "신청마감",
        ),
        Gisu(
            "etc",
            "GISU_000000000000001",
            "2026년 특화교육 1기",
            "2026-07-01",
            "2026-07-31",
            "2026-07-15",
            "2026-08-31",
            "신청중",
        ),
    ]


def _courses() -> list[Course]:
    return [
        Course(
            "601",
            "GISU_000000000000006",
            "문화예술",
            "춘천 도예교실",
            "화 10:00~12:00 / 305호",
            "춘천시민",
            "8/20(5)",
            "신청중",
            "2026-07-01",
            "2026-07-31",
            "2026-07-10",
            "2026-09-20",
        ),
        Course(
            "501",
            "GISU_000000000000005",
            "인문교양",
            "지난 인문학",
            "수 10:00~12:00",
            "성인",
            "20/20(0)",
            "신청마감",
            "2025-01-01",
            "2025-01-31",
            "2025-02-01",
            "2025-06-01",
        ),
        Course(
            "401",
            "GISU_000000000000004",
            "미래IT",
            "온라인 인공지능 입문",
            "목 19:00~21:00",
            "청소년 및 성인",
            "0/30(10)",
            "신청대기",
            "2026-08-01",
            "2026-08-15",
            "2026-08-20",
            "2026-09-30",
            education_method="온라인",
        ),
        Course(
            "201",
            "GISU_000000000000002",
            "평생교육강사",
            "교육 관계자 역량강화",
            "금 13:00~17:00 / 403호",
            "평생교육 관계자",
            "24/24(10)",
            "신청마감",
            "2026-06-01",
            "2026-06-20",
            "2026-06-10",
            "2026-10-20",
        ),
        Course(
            "101",
            "GISU_000000000000001",
            "특별교육",
            "시민 약초학교",
            "토 09:00~12:00 / 야외",
            "춘천시민",
            "5/25(5)",
            "신청중",
            "2026-07-01",
            "2026-07-31",
            "2026-07-15",
            "2026-08-20",
        ),
    ]


def _gisu_html(rows: list[Gisu]) -> str:
    body = "".join(
        f"""
        <tr>
          <td><a href="#lnk" onclick="fn_classList('{row.identity}')">{escape(row.name)}</a></td>
          <td>{row.apply_start} 09:00 ~ {row.apply_end} 18:00</td>
          <td>{row.start} ~ {row.end}</td>
          <td>{row.source_status}</td>
        </tr>
        """
        for row in rows
    )
    return f"""
      <html><head><title>춘천시 평생학습관</title></head><body>
        <form action="/site/edu/edu_class_list.do" method="post">
          <input name="e_type" value="{rows[0].e_type}">
          <input name="yy" value="">
        </form>
        <table class="tbl_bbs"><thead><tr>
          <th>기수명</th><th>접수기간</th><th>교육기간</th><th>상태</th>
        </tr></thead><tbody>{body}</tbody></table>
      </body></html>
    """


def _class_html(gisu: Gisu, rows: list[Course], *, malformed_empty: bool = False) -> str:
    if not rows:
        text = "교육이 없을 수도 있습니다." if malformed_empty else "등록된 교육이 없습니다."
        body = f'<tr class="no-contents"><td colspan="6">{text}</td></tr>'
    else:
        body = "".join(
            f"""
            <tr>
              <td>{escape(row.category)}</td>
              <td><span class="label">{escape(row.education_method)}</span>
                <a href="#lnk" onclick="fn_classView('{row.identity}')">{escape(row.title)}</a></td>
              <td>{escape(row.schedule)}</td>
              <td>{escape(row.target)}</td>
              <td><a href="javascript:edu_app_pop('{row.identity}');">{row.capacity}</a></td>
              <td><a href="#lnk" onclick="fn_classView('{row.identity}')">{row.source_status}</a></td>
            </tr>
            """
            for row in rows
        )
    return f"""
      <html><head><title>춘천시 평생학습관</title></head><body>
        <form method="post">
          <input name="e_type" value="{gisu.e_type}">
          <input name="yy" value="{gisu.identity}">
          <input name="class_no" value="">
        </form>
        <table class="tbl_bbs"><thead><tr>
          <th>구분</th><th>교육과목</th><th>강의시간</th><th>대상자</th>
          <th>접수인원/정원 (대기인원)</th><th>상태</th>
        </tr></thead><tbody>{body}</tbody></table>
      </body></html>
    """


def _detail_html(
    course: Course,
    gisu: Gisu,
    *,
    title: str | None = None,
    form_identity: str | None = None,
    control: bool | None = None,
    target: str | None = None,
) -> str:
    visible_control = course.source_status in {"신청중", "접수중", "신청하기"}
    if control is not None:
        visible_control = control
    control_html = (
        '<a class="btn" href="#lnk" onclick="edu_login();">신청하기</a>'
        if visible_control
        else ""
    )
    fields = [
        ("분야", course.category),
        ("교육방법", course.education_method),
        ("강좌명", title if title is not None else course.title),
        ("선정방식", course.selection),
        ("접수현황", course.capacity),
        ("대상자", target if target is not None else course.target),
        ("접수기간", f"{course.apply_start} 09:00 ~ {course.apply_end} 18:00"),
        ("교육기간", f"{course.start} ~ {course.end}"),
        ("교육시간", course.schedule),
        ("수강료", course.fee),
        ("재료비", "없음"),
        ("강사명", "홍길동"),
        ("강의계획서", "개인정보 가능 첨부"),
        ("강의방법", "담당 강사가 작성한 자유서술"),
        ("교육내용", "문의 033-245-5182 / 저장하면 안 되는 자유 본문"),
    ]
    body = "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>" for label, value in fields
    )
    return f"""
      <html><head><title>춘천시 평생학습관</title></head><body>
        <div class="cont clearfix"><table class="bbs_form"><tbody>{body}</tbody></table>
          <div class="btn_wrap"><a href="#lnk">목록</a>{control_html}</div>
          <form id="edu_form" action="/site/edu/edu_class_regist.do" method="post">
            <input name="class_no" value="{form_identity or course.identity}">
            <input name="yy" value="{gisu.identity}">
            <input name="e_type" value="{gisu.e_type}">
            <input id="receive" value="8">
          </form>
        </div>
        <script>function edu_login() {{ location.href='/site/mypage/login.do'; }}</script>
      </body></html>
    """


class Fixture:
    def __init__(self) -> None:
        self.gisus = _gisus()
        self.courses = _courses()
        self.calls: list[tuple[str, str, Mapping[str, str] | None]] = []
        self.counts: Counter[tuple[str, str, str]] = Counter()
        self.gisu_unstable_type = ""
        self.class_unstable_key: tuple[str, str] | None = None
        self.pagination_key: tuple[str, str] | None = None
        self.malformed_empty = False
        self.duplicate_identity = False
        self.detail_mode = ""

    def __call__(
        self,
        _session: Any,
        method: str,
        url: str,
        _timeout: int,
        data: Mapping[str, str] | None,
    ) -> Response:
        copied = dict(data) if data is not None else None
        self.calls.append((method, url, copied))
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == chuncheon.CHUNCHEON_GISU_PATH:
            e_type = query["e_type"][0]
            key = ("gisu", e_type, "")
            self.counts[key] += 1
            rows = [row for row in self.gisus if row.e_type == e_type]
            html = _gisu_html(rows)
            if self.gisu_unstable_type == e_type and self.counts[key] > 1:
                html = html.replace(rows[0].name, rows[0].name + " 변경", 1)
            return Response(html, url)
        if parsed.path == chuncheon.CHUNCHEON_CLASS_LIST_PATH:
            assert method == "POST" and copied is not None
            e_type, gisu_id = copied["e_type"], copied["yy"]
            key = ("class", e_type, gisu_id)
            self.counts[key] += 1
            gisu = next(row for row in self.gisus if row.identity == gisu_id)
            rows = [row for row in self.courses if row.gisu_identity == gisu_id]
            if self.duplicate_identity and gisu_id == "GISU_000000000000004":
                rows = [replace(rows[0], identity="601")]
            html = _class_html(
                gisu,
                rows,
                malformed_empty=self.malformed_empty and gisu_id == "GISU_000000000000003",
            )
            if self.class_unstable_key == (e_type, gisu_id) and self.counts[key] > 1:
                first = rows[0]
                html = html.replace(first.title, first.title + " 변경", 1)
            if self.pagination_key == (e_type, gisu_id):
                html = html.replace("</body>", '<div class="paging"><a href="?page=2">2</a></div></body>')
            return Response(html, url)
        if parsed.path == chuncheon.CHUNCHEON_CLASS_DETAIL_PATH:
            identity = query["class_no"][0]
            course = next(row for row in self.courses if row.identity == identity)
            gisu = next(row for row in self.gisus if row.identity == course.gisu_identity)
            kwargs: dict[str, Any] = {}
            if self.detail_mode == "title_mismatch" and identity == "601":
                kwargs["title"] = "다른 강좌"
            elif self.detail_mode == "identity_mismatch" and identity == "601":
                kwargs["form_identity"] = "999"
            elif self.detail_mode == "pii_target" and identity == "601":
                kwargs["target"] = "담당자 033-245-5182"
            elif self.detail_mode == "open_without_control" and identity == "601":
                kwargs["control"] = False
            elif self.detail_mode == "inactive_with_control" and identity == "201":
                kwargs["control"] = True
            return Response(_detail_html(course, gisu, **kwargs), url)
        raise AssertionError(f"unexpected/private endpoint request: {method} {url}")


def _target(**changes: str) -> dict[str, str]:
    target = {
        "provider": chuncheon.CHUNCHEON_PROVIDER,
        "url": chuncheon.CHUNCHEON_CANONICAL_URL,
        "candidate_id": chuncheon.CHUNCHEON_CANONICAL_CANDIDATE_ID,
    }
    target.update(changes)
    return target


def _collect(fixture: Fixture, **kwargs):
    return chuncheon.collect(
        _target(),
        today="2026-07-22",
        timeout=5,
        max_pages=20,
        detail_limit=20,
        session_factory=DummySession,
        requester=fixture,
        **kwargs,
    )


def test_constants_exact_target_and_owner_boundaries() -> None:
    assert chuncheon.CHUNCHEON_PROVIDER == "MUNI_CLC_CHUNCHEON_GO_KR_A560168D"
    assert chuncheon.CHUNCHEON_CANONICAL_CANDIDATE_ID == "MUNI_IR_68BAB2356B75"
    assert set(chuncheon.CHUNCHEON_E_TYPES) == {"jung", "dan", "etc"}
    assert chuncheon.is_target(_target())
    assert not chuncheon.is_target(_target(provider="WRONG"))
    assert not chuncheon.is_target(_target(url=chuncheon.CHUNCHEON_CANONICAL_URL + "?e_type=jung"))
    assert not chuncheon.is_target(_target(url=chuncheon.CHUNCHEON_CANONICAL_URL + "#top"))
    assert chuncheon.is_chuncheon_home_alias_target(
        {"provider": chuncheon.CHUNCHEON_HOME_PROVIDER, "url": chuncheon.CHUNCHEON_HOME_URL}
    )
    assert chuncheon.is_chuncheon_bwb_separate_target(
        {"provider": chuncheon.CHUNCHEON_BWB_PROVIDER, "url": "https://bwb.chuncheon.go.kr/"}
    )
    assert (
        chuncheon.CHUNCHEON_OWNER_BOUNDARY_AUDIT[chuncheon.CHUNCHEON_HOME_PROVIDER][
            "decision"
        ]
        == f"home_alias_of_{chuncheon.CHUNCHEON_PROVIDER}"
    )
    assert (
        chuncheon.CHUNCHEON_OWNER_BOUNDARY_AUDIT[chuncheon.CHUNCHEON_BWB_PROVIDER][
            "decision"
        ]
        == "keep_separate_new_integrated_learning_platform_owner"
    )
    with pytest.raises(ValueError):
        chuncheon.chuncheon_detail_url("other", "GISU_000000000000001", "1")
    with pytest.raises(ValueError):
        chuncheon.chuncheon_detail_url("etc", "../1", "1")


def test_complete_walk_current_details_status_branch_and_pii_allowlist() -> None:
    fixture = Fixture()
    rows, parser, meta = _collect(fixture)

    assert parser == chuncheon.CHUNCHEON_PARSER
    assert [row["raw_fields"]["identity"] for row in rows] == ["601", "401", "201", "101"]
    assert meta["gisu_counts"] == {"jung": 2, "dan": 2, "etc": 2}
    assert meta["source_gisu_total"] == 6
    assert meta["source_total"] == meta["source_rows"] == 5
    assert meta["source_type_counts"] == {"jung": 2, "dan": 1, "etc": 2}
    assert meta["source_status_counts"] == {"신청중": 2, "신청마감": 2, "신청대기": 1}
    assert meta["declared_class_pages"] == meta["data_pages"] == 6
    assert meta["gisu_stability_rechecks"] == 3
    assert meta["class_stability_rechecks"] == 6
    assert meta["stability_rechecks"] == 9
    assert meta["required_list_requests"] == meta["list_requests"] == 18
    assert meta["empty_class_page_count"] == 1
    assert meta["empty_class_pages"] == ["dan:GISU_000000000000003"]
    assert meta["current_candidate_count"] == 4
    assert meta["archived_rows_skipped_before_detail"] == 1
    assert meta["detail_attempts"] == meta["detail_pages"] == 4
    assert meta["current_source_count"] == meta["returned_count"] == 4
    assert meta["expired_count"] == 1
    assert meta["status_counts"] == {"OPEN": 2, "SCHEDULED": 1, "CLOSED": 1}
    assert meta["branch_counts"] == {"춘천시 평생학습관": 4}
    assert meta["application_control_count"] == 2
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""

    opened, scheduled, closed, second_open = rows
    assert opened["application_url"] == opened["raw_url"]
    assert opened["reservation_available"] is True
    assert scheduled["application_url"] == ""
    assert scheduled["venue_name"] == "온라인"
    assert closed["status"] == "CLOSED"
    assert second_open["branch"] == "춘천시 평생학습관"
    assert all(row["program_type"] == "교육" for row in rows)
    assert all(row["municipality_code"] == "5111000000" for row in rows)
    assert all(row["description"] == row["title"] for row in rows)

    payload = repr(rows)
    for forbidden in (
        "홍길동",
        "033-245-5182",
        "저장하면 안 되는",
        "instructor",
        "contact",
        "attachments",
        "source_html",
    ):
        assert forbidden not in payload
    assert meta["pii_payload_persisted"] is False
    assert meta["forbidden_applicant_endpoint_requests"] == 0
    assert not any(
        urlparse(url).path
        in {
            chuncheon.CHUNCHEON_APPLICANT_POPUP_PATH,
            chuncheon.CHUNCHEON_APPLICATION_FORM_PATH,
        }
        for _method, url, _data in fixture.calls
    )


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"max_pages": 5}, "max_pages cap"),
        ({"detail_limit": 3}, "detail_limit cap"),
    ],
)
def test_caps_fail_closed_without_partial_output(kwargs: dict[str, int], expected: str) -> None:
    fixture = Fixture()
    rows, _parser, meta = chuncheon.collect(
        _target(),
        today="2026-07-22",
        timeout=5,
        max_pages=kwargs.get("max_pages", 20),
        detail_limit=kwargs.get("detail_limit", 20),
        session_factory=DummySession,
        requester=fixture,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert expected in meta["configured_collection_error"]
    assert not any(urlparse(url).path == chuncheon.CHUNCHEON_CLASS_DETAIL_PATH for _, url, _ in fixture.calls)


def test_cohort_catalogue_stability_change_fails_closed() -> None:
    fixture = Fixture()
    fixture.gisu_unstable_type = "jung"
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "cohort catalogue stability recheck changed" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


def test_class_boundary_stability_change_fails_closed() -> None:
    fixture = Fixture()
    fixture.class_unstable_key = ("etc", "GISU_000000000000001")
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "class boundary stability recheck changed" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


def test_declared_single_page_contract_rejects_hidden_pagination() -> None:
    fixture = Fixture()
    fixture.pagination_key = ("jung", "GISU_000000000000006")
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "unexpectedly exposes pagination" in meta["configured_collection_error"]


def test_empty_class_page_requires_structural_sentinel() -> None:
    fixture = Fixture()
    fixture.malformed_empty = True
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "structural empty sentinel changed" in meta["configured_collection_error"]


def test_duplicate_official_class_identity_fails_before_details() -> None:
    fixture = Fixture()
    fixture.duplicate_identity = True
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "duplicate official class identities" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("title_mismatch", "list/detail title mismatch"),
        ("identity_mismatch", "detail identity mismatch"),
        ("pii_target", "unsafe course 601 대상자"),
        ("open_without_control", "no unique public application control"),
        ("inactive_with_control", "inactive status exposes an application control"),
    ],
)
def test_detail_application_and_privacy_contracts_fail_closed(mode: str, expected: str) -> None:
    fixture = Fixture()
    fixture.detail_mode = mode
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert expected in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_dedupe_may_not_reduce_official_identity_cardinality() -> None:
    fixture = Fixture()
    rows, _parser, meta = _collect(fixture, dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed official identity cardinality" in meta["configured_collection_error"]


def test_wrong_target_and_invalid_limits_return_no_rows() -> None:
    fixture = Fixture()
    rows, _parser, meta = chuncheon.collect(
        _target(provider="WRONG"), session_factory=DummySession, requester=fixture
    )
    assert rows == []
    assert "canonical Chuncheon CLC owner" in meta["configured_collection_error"]

    rows, _parser, meta = chuncheon.collect(
        _target(), max_pages=0, session_factory=DummySession, requester=fixture
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "invalid collection limits" in meta["configured_collection_error"]


def test_discovery_audit_records_full_live_boundary() -> None:
    audit = chuncheon.CHUNCHEON_DISCOVERY_AUDIT
    assert audit["gisu_counts"] == {"jung": 6, "dan": 11, "etc": 18}
    assert audit["class_counts"] == {"jung": 341, "dan": 295, "etc": 152}
    assert audit["gisu_total"] == 35
    assert audit["source_total"] == audit["unique_class_identities"] == 788
    assert audit["source_status_counts"] == {"신청마감": 788}
    assert audit["current_or_future"] == 0


@pytest.mark.skipif(
    os.getenv("CHUNCHEON_EDUCATION_LIVE") != "1",
    reason="set CHUNCHEON_EDUCATION_LIVE=1 for official-source verification",
)
def test_live_complete_legacy_snapshot() -> None:
    rows, parser, meta = chuncheon.collect(
        _target(),
        today=date(2026, 7, 22),
        timeout=40,
        max_pages=40,
        detail_limit=100,
    )
    assert parser == chuncheon.CHUNCHEON_PARSER
    assert rows == []
    assert meta["configured_collection_error"] == ""
    assert meta["gisu_counts"] == {"jung": 6, "dan": 11, "etc": 18}
    assert meta["source_gisu_total"] == 35
    assert meta["source_type_counts"] == {"jung": 341, "dan": 295, "etc": 152}
    assert meta["source_total"] == 788
    assert meta["source_status_counts"] == {"신청마감": 788}
    assert meta["empty_class_page_count"] == 2
    assert meta["required_list_requests"] == meta["list_requests"] == 47
    assert meta["stability_rechecks"] == 9
    assert meta["current_candidate_count"] == 0
    assert meta["detail_pages"] == 0
    assert meta["expired_count"] == 788
    assert meta["no_current_data"] is True
    assert meta["snapshot_complete"] is True
