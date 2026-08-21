from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
from html import escape
import os
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_jeongseon as jeongseon


@dataclass
class Target:
    provider: str
    url: str
    candidate_id: str = ""


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    category: str = "평생교육강좌"
    apply_start: str = "2026-01-01"
    apply_end: str = "2026-01-10"
    start: str = "2026-01-15"
    end: str = "2026-02-15"
    schedule: str = "화 : 10:00 ~ 12:00"
    status: str = "신청종료"
    capacity_current: int = 2
    capacity_total: int = 10
    venue: str = "정선군평생학습관 > 배움실"
    detail_status: str = "마감"


class DummySession:
    def close(self) -> None:
        return None


def _target() -> Target:
    return Target(
        jeongseon.JEONGSEON_PROVIDER,
        jeongseon.JEONGSEON_REGISTERED_URL,
        jeongseon.JEONGSEON_CANONICAL_CANDIDATE_ID,
    )


def _courses(*, current: bool = True) -> list[Course]:
    rows = [
        Course(str(number), f"과거 정선 강좌 {number}")
        for number in range(12, 0, -1)
    ]
    if current:
        rows[0] = Course(
            "12",
            "현재 온라인 강좌",
            apply_start="2026-07-01",
            apply_end="2026-07-31",
            start="2026-07-10",
            end="2026-12-31",
            status="신청하기",
            detail_status="강의중",
            venue=(
                "아리샘터 음악연습 > 강원 정선군 정선읍 애산로 21-8 "
                "정선아리랑 생활문화센터"
            ),
        )
        rows[1] = Course(
            "11",
            "향후 기초문해 강좌",
            category="기초문해교육강좌",
            apply_start="2026-08-01",
            apply_end="2026-08-10",
            start="2026-08-15",
            end="2026-12-31",
            status="접수예정",
            detail_status="예정",
            venue="정선군문화예술회관",
        )
        rows[2] = Course(
            "10",
            "현재 디지털문해 강좌",
            category="디지털문해교육강좌",
            apply_start="2026-02-01",
            apply_end="2026-02-10",
            start="2026-03-01",
            end="2026-12-31",
            status="신청종료",
            detail_status="강의중",
        )
    return rows


def _card(course: Course, *, status: str | None = None) -> str:
    def block(label: str, value: str) -> str:
        return (
            '<div class="nomargin">'
            f"<b>{escape(label)}</b><span class=\"commonValue\">{escape(value)}</span>"
            "</div>"
        )

    return f"""
      <li class="clearfix">
        <p class="tit"><a class="click_move" data-move="{course.identity}">{escape(course.title)}</a></p>
        {block("신청기간", f"{course.apply_start} ~ {course.apply_end}")}
        {block("교육기간", f"{course.start} ~ {course.end}")}
        {block("강의시간", course.schedule)}
        {block("요 약", "개인 연락처가 있을 수 있는 자유 서술")}
        <div class="util"><span class="loc">{course.capacity_current} 명 / {course.capacity_total} 명</span>
          <span class="state">{escape(status if status is not None else course.status)}</span></div>
      </li>
    """


def _list_html(
    requested_page: int,
    rows: list[Course],
    *,
    rendered_page: int | None = None,
    total_drift: bool = False,
    duplicate_identity: bool = False,
    unknown_status: bool = False,
    unstable: bool = False,
) -> str:
    page = requested_page if rendered_page is None else rendered_page
    source = list(rows)
    if duplicate_identity and len(source) > 1:
        source[1] = replace(source[1], identity=source[0].identity)
    if unstable and source:
        changed_index = min(
            (page - 1) * jeongseon.JEONGSEON_PAGE_SIZE,
            len(source) - 1,
        )
        source[changed_index] = replace(
            source[changed_index], title="변경된 강좌"
        )
    total = len(source) - (1 if total_drift else 0)
    last = max(1, (total + jeongseon.JEONGSEON_PAGE_SIZE - 1) // jeongseon.JEONGSEON_PAGE_SIZE)
    start = (page - 1) * jeongseon.JEONGSEON_PAGE_SIZE
    page_rows = source[start : start + jeongseon.JEONGSEON_PAGE_SIZE]
    body = "".join(
        _card(
            item,
            status="알수없음" if unknown_status and start + offset == 0 else None,
        )
        for offset, item in enumerate(page_rows)
    )
    return f"""
      <html><head><title>정선군평생학습관에 오신것을 환영합니다.</title></head>
      <body><main><h2>강의신청</h2>
        <p>전체 : {total} ({page}/{last} 페이지)</p>
        <div class="program_list apply_type1"><ul>{body}</ul></div>
      </main></body></html>
    """


def _detail_html(
    course: Course,
    *,
    title: str | None = None,
    control: str = "auto",
    bad_form: bool = False,
    bad_form_action: bool = False,
) -> str:
    fields = (
        ("분야", course.category),
        ("강좌명", title if title is not None else course.title),
        ("위치", course.venue),
        ("신청기간", f"{course.apply_start} ~ {course.apply_end}"),
        ("교육기간", f"{course.start} ~ {course.end}"),
        ("교육시간", course.schedule),
        ("교재정보", "없음"),
        ("정원", f"{course.capacity_current} 명 / {course.capacity_total} 명"),
        ("강의상태", course.detail_status),
        ("요약", "담당자 private@example.org / 010-1234-5678"),
    )
    table = "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>"
        for label, value in fields
    )
    if control == "auto":
        control = (
            "open"
            if course.status in {"접수중", "신청하기", "온라인신청"}
            else "inactive"
        )
    if control == "open":
        identity = "999" if bad_form else course.identity
        action = "/unexpected_apply" if bad_form_action else "/lec_apply"
        marker = f"""
          <form method="post" action="{action}">
            <input type="hidden" name="cls_no" value="{identity}">
            <label>신청자명<input name="applicant_name" value=""></label>
            <label>전화번호<input name="phone" value=""></label>
            <button type="submit">신청하기</button>
          </form>
        """
    elif control == "actionable_closed":
        marker = '<button type="button">신청하기</button>'
    elif control == "missing":
        marker = ""
    else:
        label = "접수예정" if course.status == "접수예정" else "신청종료"
        marker = f'<button type="button" disabled>{label}</button>'
    return f"""
      <html><head><title>정선군평생학습관에 오신것을 환영합니다.</title></head>
      <body><main><h2>강좌소개</h2><table>{table}</table>
        {marker}<a href="/private.pdf">개인 첨부</a>
      </main></body></html>
    """


class FixtureSite:
    def __init__(
        self,
        *,
        current: bool = True,
        boundary: str = "clamp",
        **flags: bool,
    ) -> None:
        self.rows = _courses(current=current)
        self.by_id = {item.identity: item for item in self.rows}
        self.boundary = boundary
        self.flags = flags
        self.calls: Counter[str] = Counter()
        self._page_one_calls = 0
        self._lock = Lock()

    def __call__(self, session: DummySession, url: str, timeout: int) -> str:
        assert timeout > 0
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == jeongseon.JEONGSEON_DETAIL_PATH:
            identity = query["cls_no"][0]
            self.calls["detail"] += 1
            item = self.by_id[identity]
            control = "auto"
            if self.flags.get("missing_control") and identity == "12":
                control = "missing"
            if self.flags.get("closed_actionable") and identity == "10":
                control = "actionable_closed"
            return _detail_html(
                item,
                title="다른 강좌" if self.flags.get("detail_title_mismatch") else None,
                control=control,
                bad_form=self.flags.get("bad_form", False) and identity == "12",
                bad_form_action=(
                    self.flags.get("bad_form_action", False) and identity == "12"
                ),
            )
        assert parsed.path == jeongseon.JEONGSEON_LIST_PATH
        quarter = query.get("quarter", [None])[0]
        assert quarter == "" or quarter in jeongseon.JEONGSEON_QUARTERS
        page = int(query.get("page", ["1"])[0])
        source_rows = self.rows
        if quarter:
            category = jeongseon.JEONGSEON_QUARTERS[quarter]
            source_rows = [item for item in self.rows if item.category == category]
            if self.flags.get("missing_partition") and quarter == "OnlineEdu":
                source_rows = []
        with self._lock:
            self.calls[f"list:{page}"] += 1
            self.calls[f"list:{quarter or 'all'}:{page}"] += 1
            if quarter == "" and page == 1:
                self._page_one_calls += 1
                page_one_call = self._page_one_calls
            else:
                page_one_call = 0
            current_page_call = self.calls[f"list:{quarter or 'all'}:{page}"]
        rendered_page = None
        if quarter == "" and page == 3:
            if self.boundary == "clamp":
                rendered_page = 2
            elif self.boundary == "wrong":
                rendered_page = 1
        return _list_html(
            page,
            source_rows,
            rendered_page=rendered_page,
            total_drift=(
                self.flags.get("total_drift", False)
                and quarter == ""
                and page == 2
            ),
            duplicate_identity=(
                self.flags.get("duplicate_identity", False) and quarter == ""
            ),
            unknown_status=(
                self.flags.get("unknown_status", False) and quarter == ""
            ),
            unstable=(
                self.flags.get("unstable_page_one", False) and page_one_call > 1
            )
            or (
                self.flags.get("unstable_last_page", False)
                and quarter == ""
                and page == 2
                and current_page_call > 1
            ),
        )


def _collect(site: FixtureSite, **kwargs):
    return jeongseon.collect_jeongseon_education(
        _target(),
        today="2026-07-21",
        max_workers=1,
        session_factory=DummySession,
        fetcher=site,
        **kwargs,
    )


def test_registered_owner_canonicalizes_to_unfiltered_source() -> None:
    assert _target().candidate_id == jeongseon.JEONGSEON_CANONICAL_CANDIDATE_ID
    assert jeongseon.is_jeongseon_education_target(_target())
    assert not jeongseon.is_jeongseon_education_target(
        Target(jeongseon.JEONGSEON_PROVIDER, jeongseon.JEONGSEON_ALL_URL)
    )
    assert jeongseon.is_jeongseon_unfiltered_source(
        Target("anything", jeongseon.JEONGSEON_ALL_URL)
    )
    assert jeongseon.jeongseon_all_list_url(1) == jeongseon.JEONGSEON_ALL_URL
    assert jeongseon.jeongseon_all_list_url(2).endswith("quarter=&page=2")
    assert jeongseon.jeongseon_partition_url("InfoEdu", 2).endswith(
        "quarter=InfoEdu&page=2"
    )
    assert not jeongseon.jeongseon_partition_url("BAD")
    assert not jeongseon.jeongseon_detail_url("../../etc/passwd")
    assert jeongseon._is_official_request_url(jeongseon.JEONGSEON_ALL_URL)
    assert jeongseon._is_official_request_url(
        jeongseon.jeongseon_partition_url("OnlineEdu", 2)
    )
    assert not jeongseon._is_official_request_url(
        jeongseon.JEONGSEON_ALL_URL + "&unexpected=1"
    )


def test_all_promotion_candidates_are_excluded_from_course_ownership() -> None:
    assert set(jeongseon.JEONGSEON_CANDIDATE_AUDIT) == (
        jeongseon.JEONGSEON_EXCLUDED_CANDIDATE_IDS
    )
    for candidate_id, audit in jeongseon.JEONGSEON_CANDIDATE_AUDIT.items():
        candidate = Target(str(audit["provider"]), str(audit["url"]), candidate_id)
        assert jeongseon.is_jeongseon_excluded_candidate(candidate)
        assert not jeongseon.is_jeongseon_education_target(candidate)


def test_complete_clamped_snapshot_fetches_only_current_details() -> None:
    site = FixtureSite()
    rows, parser, meta = _collect(site)

    assert parser == jeongseon.JEONGSEON_PARSER
    assert len(rows) == 3
    assert {row["provider_course_id"].rsplit(":", 1)[-1] for row in rows} == {
        "10",
        "11",
        "12",
    }
    assert {row["status"] for row in rows} == {"OPEN", "SCHEDULED", "CLOSED"}
    open_row = next(row for row in rows if row["status"] == "OPEN")
    assert open_row["application_url"] == open_row["raw_url"]
    assert open_row["application_type"] == "ONLINE_RESERVATION"
    assert open_row["reservation_available"] is True
    assert open_row["branch"] == "아리샘터 음악연습"
    assert open_row["venue"].endswith("정선아리랑 생활문화센터")
    assert open_row["raw_fields"]["application_control_identity"] == "12"
    assert open_row["raw_fields"]["application_control_method"] == "POST"
    assert (
        open_row["raw_fields"]["application_control_action"]
        == jeongseon.JEONGSEON_APPLICATION_PATH
    )
    assert all(row["raw_fields"]["detail_verified"] for row in rows)
    assert all(row["raw_fields"]["application_control_verified"] for row in rows)
    assert meta["source_total"] == meta["source_rows"] == 12
    assert meta["declared_pages"] == 2
    assert meta["list_requests"] == meta["required_list_requests"] == 8
    assert meta["boundary_mode"] == "exact_last_page_clamp"
    assert meta["stability_rechecks"] == 2
    assert meta["partition_requests"] == 3
    assert meta["partitions_complete"] is True
    assert meta["partition_declared_counts"] == {
        "LifeEdu": 10,
        "InfoEdu": 1,
        "OnlineEdu": 1,
    }
    assert meta["current_source_count"] == 3
    assert meta["detail_attempts"] == meta["detail_pages"] == 3
    assert meta["online_open_count"] == 1
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert meta["branch_counts"] == {
        "아리샘터 음악연습": 1,
        "정선군문화예술회관": 1,
        "정선군평생학습관": 1,
    }
    assert site.calls["list:all:1"] == 2
    assert site.calls["list:all:2"] == 2
    assert site.calls["list:3"] == 1
    assert site.calls["detail"] == 3


def test_empty_sentinel_is_also_a_valid_exact_boundary() -> None:
    rows, _, meta = _collect(FixtureSite(boundary="empty"))
    assert len(rows) == 3
    assert meta["boundary_mode"] == "empty_sentinel"
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True


def test_pii_freeform_and_application_inputs_are_not_persisted() -> None:
    rows, _, meta = _collect(FixtureSite())
    payload = repr(rows)
    assert "private@example.org" not in payload
    assert "010-1234-5678" not in payload
    assert "개인 연락처" not in payload
    assert "applicant_name" not in payload
    assert "private.pdf" not in payload
    assert all(row["description"] == row["title"] for row in rows)
    assert all(
        set(row["raw_fields"]) <= jeongseon._SAFE_RAW_FIELDS for row in rows
    )
    assert meta["pii_payload_persisted"] is False


def test_complete_historical_snapshot_is_valid_zero_current_data() -> None:
    site = FixtureSite(current=False)
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["source_rows"] == 12
    assert meta["current_source_count"] == 0
    assert meta["detail_attempts"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["configured_collection_error"] == ""


@pytest.mark.parametrize(
    "site,error_fragment",
    [
        (FixtureSite(boundary="wrong"), "neither sentinel nor clamp"),
        (FixtureSite(total_drift=True), "total/page/last changed"),
        (FixtureSite(duplicate_identity=True), "duplicate source identities"),
        (FixtureSite(unknown_status=True), "public list status changed"),
        (FixtureSite(unstable_page_one=True), "page-one stability recheck changed"),
        (FixtureSite(unstable_last_page=True), "last-page stability recheck changed"),
        (FixtureSite(missing_partition=True), "partition totals"),
    ],
)
def test_list_boundary_and_identity_drift_fail_closed(
    site: FixtureSite, error_fragment: str
) -> None:
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error_fragment in meta["configured_collection_error"]


@pytest.mark.parametrize(
    "flag,error_fragment",
    [
        ("detail_title_mismatch", "title list/detail mismatch"),
        ("missing_control", "open application control changed"),
        ("bad_form", "form identity"),
        ("bad_form_action", "form method/action changed"),
        ("closed_actionable", "inactive course exposes application control"),
    ],
)
def test_detail_and_application_contract_drift_fails_closed(
    flag: str, error_fragment: str
) -> None:
    rows, _, meta = _collect(FixtureSite(**{flag: True}))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error_fragment in meta["configured_collection_error"]


def test_caps_and_wrong_owner_do_not_return_partial_rows() -> None:
    rows, _, meta = _collect(FixtureSite(), max_pages=3)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "8 required list requests" in meta["configured_collection_error"]

    rows, _, meta = _collect(FixtureSite(), detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "3 required current details" in meta["configured_collection_error"]

    rows, _, meta = jeongseon.collect_jeongseon_education(
        Target("wrong", jeongseon.JEONGSEON_REGISTERED_URL),
        fetcher=lambda *_: pytest.fail("wrong owner must not fetch"),
    )
    assert rows == []
    assert "registered Jeongseon education owner" in meta["configured_collection_error"]


def test_post_dedupe_pii_mutation_is_rejected() -> None:
    def mutate(rows):
        rows[0]["phone"] = "010-1234-5678"
        return rows

    rows, _, meta = _collect(FixtureSite(), dedupe_rows=mutate)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "forbidden PII" in meta["configured_collection_error"]


def test_deduper_cannot_change_official_identity_cardinality() -> None:
    rows, _, meta = _collect(FixtureSite(), dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "dedupe changed official identity cardinality" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("JEONGSEON_EDUCATION_LIVE") != "1",
    reason="set JEONGSEON_EDUCATION_LIVE=1 for the official live audit",
)
def test_live_official_complete_snapshot() -> None:
    rows, parser, meta = jeongseon.collect_jeongseon_education(
        _target(),
        timeout=40,
        max_pages=120,
        detail_limit=500,
        max_workers=8,
    )

    assert parser == jeongseon.JEONGSEON_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["partitions_complete"] is True
    assert meta["details_complete"] is True
    assert set(meta["partition_declared_counts"]) == set(
        jeongseon.JEONGSEON_QUARTERS
    )
    assert sum(meta["partition_declared_counts"].values()) == meta["source_total"]
    assert meta["source_rows"] == meta["source_total"]
    assert meta["returned_count"] == len(rows)
    assert all(row["end_date"] >= date.today().isoformat() for row in rows)
    assert all(row["municipality_code"] == "5177000000" for row in rows)
