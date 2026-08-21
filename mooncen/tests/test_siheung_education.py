from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import html
import json
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from Crawler import municipal_siheung as siheung


@dataclass
class Target:
    provider: str = siheung.SIHEUNG_PROVIDER
    url: str = siheung.SIHEUNG_URL
    branch: str = "경기도 시흥시"


class FakeResponse:
    def __init__(self, *, url: str, text: str = "", payload: Any = None) -> None:
        self.url = url
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = 200
        self.history: list[Any] = []
        self._payload = payload

    def json(self) -> Any:
        return deepcopy(self._payload)


def _item(
    identity: int,
    *,
    total: int = 10,
    status: str = "마감",
    start: str = "2026.06.01",
    end: str = "2026.07.21",
    title: str | None = None,
    region: str = "대야동",
    branch: str = "시흥시 평생학습과",
) -> dict[str, Any]:
    return {
        "d_sbjct_sn": str(identity),
        "d_sbjct_cycl_sn": "1",
        "d_sbjct_nm": title or f"합성 교육 {identity}",
        "d_co_sprvsn_id": siheung.SIHEUNG_CO_SPONSOR_ID,
        "d_edu_gvmnfc": branch,
        "d_rgn": region,
        "d_recrut_stts_nm": status,
        "d_sbjct_type_cd_id": "OF",
        "d_is_single_day_course": "N",
        "d_edu_bgng_dt": start,
        "d_edu_end_dt": end,
        "d_edu_start_time": "10:00",
        "d_edu_end_time": "12:00",
        "d_edu_wday_cd_nm": "화",
        "d_edu_nope": "20",
        "d_aply_cnt": "3",
        "d_sbjct_amt": "10000",
        "d_prepar_cmdty_amt": "0",
        "d_sbjct_trgt_nm_1": "시흥시민",
        "d_sbjct_intrd_cn": f"안전한 합성 강좌 소개 {identity}",
        "d_instr_nm": f"합성강사{identity}",
        "d_stdnt_chice_mthd_cd_nm": "선착",
        "d_clsf_depth1_nm": "인문교양",
        "d_clsf_depth2_nm": "시민교육",
        "d_clsf_depth3_nm": "",
        "d_total_cnt": str(total),
    }


def _source_rows() -> list[dict[str, Any]]:
    rows = [
        _item(
            1001,
            status="모집중",
            start="2026.08.01",
            end="2026.08.31",
            title="합성 &lt;열린 강좌&gt;",
        ),
        _item(
            1002,
            status="모집예정",
            start="2026.09.01",
            end="2026.09.30",
            region="정왕1동",
            branch="배곧1동 주민자치회",
        ),
    ]
    rows.extend(_item(identity) for identity in range(1003, 1011))
    return rows


def _dot_date(value: str) -> str:
    return value + ".(화)"


def _detail_html(
    item: dict[str, Any],
    *,
    venue: str,
    bad_kind: str = "",
) -> str:
    subject = item["d_sbjct_sn"]
    cycle = item["d_sbjct_cycl_sn"]
    source_status = item["d_recrut_stts_nm"]
    title = html.unescape(item["d_sbjct_nm"])
    if bad_kind == "title":
        title = "다른 상세 제목"
    if source_status == "모집예정":
        control = '<span class="btn-course-apply disabled">모집예정</span>'
        application_periods = """
          <dt>일반신청기간</dt><dd>2026.08.01.(토) ~ 2026.08.10.(월)</dd>
          <dt>추가신청기간</dt><dd>2026.08.11.(화) ~ 2026.08.15.(토)</dd>
        """
    else:
        control = '<a class="btn-course-apply usetap" href="javascript:void(0);">수강신청</a>'
        application_periods = "<dt>신청기간</dt><dd>2026.07.01.(수) ~ 2026.07.31.(금)</dd>"
    if bad_kind == "control":
        control = '<span class="btn-course-apply disabled">신청마감</span>'
    if bad_kind == "container":
        return "<html><body><p>missing</p></body></html>"
    description = "공개 강좌 설명"
    if subject == "1001":
        description = "개인 문의 tester@example.com / 010-1234-5678"
    return f"""
    <html><body>
      <input name="s_sbjct_sn" value="{subject}">
      <input name="s_sbjct_cycl_sn" value="{cycle}">
      <input name="p_return_url"
             value="/user/course/offline/view?s_sbjct_sn={subject}&amp;s_sbjct_cycl_sn={cycle}">
      <div class="course-detail-container">
        <section class="key-course-info">
          <span class="tag-item-xs">{source_status}</span>
          <span class="tag-type offline-type">{item["d_rgn"]}</span>
          <span class="tag-field">{item["d_edu_gvmnfc"]}</span>
          <h2 class="course-title">{html.escape(title)}</h2>
          <p class="course-desc">{description}</p>
          <dl>
            {application_periods}
            <dt>학습기간</dt>
            <dd>{_dot_date(item["d_edu_bgng_dt"])} ~ {_dot_date(item["d_edu_end_dt"])}</dd>
            <dt>교육시간</dt><dd>매주 화 10:00 ~ 12:00</dd>
          </dl>
          <dl>
            <dt>교육대상</dt><dd>시흥시민</dd>
            <dt>모집인원</dt><dd>총 20 명</dd>
            <dt>수강료</dt><dd>10,000원</dd>
            <dt>교육장소</dt><dd>{venue}</dd>
          </dl>
          <dl><dt>경력</dt><dd>비공개 경력 010-9999-8888</dd></dl>
        </section>
        <div class="btn-course-box">{control}</div>
      </div>
      <script>
        const verify = '/user/course/cert/checkCi';
        const apply = '/user/course/aply';
      </script>
    </body></html>
    """


class Backend:
    def __init__(self) -> None:
        self.rows = _source_rows()
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.post_hits: Counter[int] = Counter()
        self.partial_start = 0
        self.sentinel_payload: list[Any] = []
        self.mutate_edge_start = 0
        self.detail_faults: dict[str, str] = {}
        self.venues: dict[str, str] = {}
        self.fail_once: Counter[tuple[str, str]] = Counter()

    def session_factory(self) -> "FakeSession":
        return FakeSession(self)

    def landing(self) -> str:
        return f"""
        <html><head><title>시흥교육캠퍼스 쏙(SSOC)</title></head><body>
          <form><input id="s_resion_cd1" name="s_resion_cd1"
                       value="{siheung.SIHEUNG_REGION_CODE}">
          <input name="ARK_CO_SPRVSN_ID"
                 value="{siheung.SIHEUNG_CO_SPONSOR_ID}"></form>
          <p>총 <span>{len(self.rows)}</span>개의 강좌가 있습니다.</p>
          <script>const endpoint = '{siheung.SIHEUNG_LIST_PATH}/search';</script>
        </body></html>
        """

    def maybe_fail(self, method: str, url: str) -> None:
        key = (method, url)
        if self.fail_once[key] > 0:
            self.fail_once[key] -= 1
            raise TimeoutError("synthetic transient failure")


class FakeSession:
    def __init__(self, backend: Backend) -> None:
        self.backend = backend
        self.headers: dict[str, str] = {}

    def close(self) -> None:
        pass

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.backend.calls.append(("GET", url, deepcopy(kwargs)))
        self.backend.maybe_fail("GET", url)
        if url == siheung.SIHEUNG_URL:
            return FakeResponse(url=url, text=self.backend.landing())
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == siheung.SIHEUNG_HOST
        assert parsed.path == siheung.SIHEUNG_DETAIL_PATH
        query = parse_qs(parsed.query)
        subject = query["s_sbjct_sn"][0]
        cycle = query["s_sbjct_cycl_sn"][0]
        item = next(
            row for row in self.backend.rows if row["d_sbjct_sn"] == subject and row["d_sbjct_cycl_sn"] == cycle
        )
        identity = f"{subject}:{cycle}"
        venue = self.backend.venues.get(identity, f"경기 시흥시 시청로 20 합성강의실 {subject}")
        return FakeResponse(
            url=url,
            text=_detail_html(
                item,
                venue=venue,
                bad_kind=self.backend.detail_faults.get(identity, ""),
            ),
        )

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.backend.calls.append(("POST", url, deepcopy(kwargs)))
        self.backend.maybe_fail("POST", url)
        assert url == siheung.SIHEUNG_API_URL
        data = kwargs["data"]
        assert set(data) == {"s_sort_by", "s_row_start", "s_row_end", "resion"}
        assert data["s_sort_by"] == "1"
        assert data["resion"] == siheung.SIHEUNG_REGION_CODE
        start = int(data["s_row_start"])
        end = int(data["s_row_end"])
        assert end - start == siheung.SIHEUNG_PAGE_SIZE
        self.backend.post_hits[start] += 1
        if start == len(self.backend.rows) + 1:
            payload = self.backend.sentinel_payload
        elif start == self.backend.partial_start:
            payload = []
        else:
            payload = self.backend.rows[start - 1 : end - 1]
        payload = deepcopy(payload)
        if start == self.backend.mutate_edge_start and self.backend.post_hits[start] >= 2 and payload:
            payload[0]["d_sbjct_nm"] += " 변경"
        return FakeResponse(url=url, payload=payload)


def _collect(
    backend: Backend,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    return siheung.collect_siheung_education_courses(
        Target(),
        today=date(2026, 7, 23),
        session_factory=backend.session_factory,
        sleeper=lambda _: None,
        **kwargs,
    )


def test_target_urls_ranges_and_budget_constants_are_strict() -> None:
    assert siheung.SIHEUNG_PROVIDER == "MUNI_WWW_SIHEUNG_GO_KR_0A4570AD"
    assert siheung.SIHEUNG_CANDIDATE_ID == "MUNI_IR_94BB80AE44E6"
    assert siheung.SIHEUNG_CO_SPONSOR_ID == "G000002"
    assert siheung.SIHEUNG_PARENT_PROVIDER == "GYEONGGI_GSEEK"
    assert siheung.SIHEUNG_PARENT_URL == ("https://www.gseek.kr/user/course/offline/list")
    assert siheung.SIHEUNG_PAGE_SIZE == 9
    assert siheung.SIHEUNG_DEFAULT_MAX_PAGES >= 200
    assert siheung.SIHEUNG_DEFAULT_DETAIL_LIMIT >= 310
    assert siheung.SIHEUNG_DEFAULT_MAX_REQUESTS >= 513
    assert len(siheung.SIHEUNG_AUDITED_BRANCHES) == 29
    assert len(siheung.SIHEUNG_EXCLUDED_TEST_ROWS) == 5
    assert siheung.SIHEUNG_LEGACY_URL.endswith("/reservation/edu/program/list.do")
    assert siheung.SIHEUNG_SEPARATE_SPORTS_OWNER_URL == "https://sportsapp.shsi.or.kr/fmcs/3"
    assert siheung.is_siheung_education_target(Target())
    assert not siheung.is_siheung_education_target(Target(provider="WRONG"))
    for value in (
        "http://siheung.gseek.kr/user/course/offline/list",
        "https://siheung.gseek.kr:443/user/course/offline/list",
        siheung.SIHEUNG_URL + "?page=1",
        siheung.SIHEUNG_URL + "#fragment",
        siheung.SIHEUNG_PARENT_URL,
        "https://siheung.gseek.kr/user/course/offline/list/../view",
    ):
        assert not siheung.is_siheung_education_target(Target(url=value))
    assert siheung.siheung_api_range(1) == (1, 10)
    assert siheung.siheung_api_range(17) == (145, 154)
    assert siheung.siheung_api_range("../1") == (0, 0)
    assert siheung.siheung_sentinel_range(1788) == (1789, 1798)
    assert siheung.siheung_sentinel_range("bad") == (0, 0)
    assert siheung.siheung_detail_url("61834", "1").endswith("s_sbjct_sn=61834&s_sbjct_cycl_sn=1")
    assert siheung.siheung_detail_url("61834&admin=1", "1") == ""


def test_complete_post_census_details_application_and_pii_minimization() -> None:
    backend = Backend()
    rows, parser, meta = _collect(backend)

    assert parser == siheung.SIHEUNG_PARSER
    assert len(rows) == 2
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["source_total"] == meta["source_rows"] == 10
    assert meta["data_pages"] == 2
    assert meta["pages"] == 3
    assert meta["page_counts"] == {1: 9, 2: 1, 3: 0}
    assert meta["sentinel_start"] == 11
    assert meta["sentinel_end"] == 20
    assert meta["sentinel_count"] == 0
    assert meta["sentinel_kind"] == "exact_post_total_empty"
    assert meta["stability_rechecks"] == 2
    assert len(meta["first_page_signature"]) == 64
    assert len(meta["last_page_signature"]) == 64
    assert meta["first_identity"] == "1001:1"
    assert meta["last_identity"] == "1010:1"
    assert meta["expired_count"] == 8
    assert meta["current_count"] == meta["returned_count"] == 2
    assert meta["detail_attempts"] == meta["detail_pages"] == 2
    assert meta["detail_errors"] == 0
    assert meta["required_logical_requests"] == 8
    assert meta["physical_requests"] == 8
    assert meta["list_requests"] == 5
    assert meta["retry_count"] == 0
    assert meta["source_region_counts"] == {"대야동": 9, "정왕1동": 1}
    assert meta["current_source_region_counts"] == {"대야동": 1, "정왕1동": 1}
    assert meta["branch_counts"] == {"시흥시 평생학습과": 1, "배곧1동 주민자치회": 1}
    assert meta["status_counts"] == {"OPEN": 1, "SCHEDULED": 1}
    assert meta["duplicate_count"] == 0
    assert meta["duplicate_url_count"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert meta["test_or_notice_row_count"] == 0
    assert meta["pii_redaction_count"] == 1
    assert meta["application_control_count"] == 2
    assert meta["reservation_discovery_links"] == 1
    assert meta["application_endpoints_called"] == 0
    assert meta["parent_aggregate_exclusion_required"] is True
    assert meta["parent_aggregate_exclusion_field"] == "d_co_sprvsn_id"
    assert meta["parent_aggregate_exclusion_value"] == "G000002"
    assert meta["parent_aggregate_overlap_identity"] == ("d_sbjct_sn+d_sbjct_cycl_sn")

    opened, scheduled = rows
    assert opened["title"] == "합성 <열린 강좌>"
    assert opened["provider"] == siheung.SIHEUNG_PROVIDER
    assert opened["provider_course_id"].endswith(":course:1001:1")
    assert opened["branch"] == "시흥시 평생학습과"
    assert opened["region"] == siheung.SIHEUNG_MUNICIPALITY_NAME
    assert opened["venue_name"].endswith("합성강의실 1001")
    assert opened["reservation_available"] is True
    assert opened["application_url"] == opened["raw_url"]
    assert opened["application_type"] == "ONLINE_RESERVATION"
    assert opened["description"] == "안전한 합성 강좌 소개 1001"
    assert scheduled["status"] == "SCHEDULED"
    assert scheduled["reservation_available"] is False
    assert "application_url" not in scheduled
    assert scheduled["apply_period"] == "2026-08-11 ~ 2026-08-15"
    assert scheduled["general_apply_period"] == "2026-08-01 ~ 2026-08-10"
    assert scheduled["additional_apply_period"] == "2026-08-11 ~ 2026-08-15"

    persisted = json.dumps(rows, ensure_ascii=False)
    assert "tester@example.com" not in persisted
    assert "010-1234-5678" not in persisted
    assert "비공개 경력" not in persisted
    assert "010-9999-8888" not in persisted
    assert "source_item" not in persisted
    assert all(call[1] not in {"/user/course/cert/checkCi", "/user/course/aply"} for call in backend.calls)
    post_ranges = [
        (int(call[2]["data"]["s_row_start"]), int(call[2]["data"]["s_row_end"]))
        for call in backend.calls
        if call[0] == "POST"
    ]
    assert post_ranges == [(1, 10), (10, 19), (11, 20), (1, 10), (10, 19)]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_pages": 2}, "max_pages cap"),
        ({"detail_limit": 1}, "detail_limit cap"),
        ({"max_requests": 7}, "max_requests cap"),
    ],
)
def test_caps_fail_closed(kwargs: dict[str, int], message: str) -> None:
    rows, _, meta = _collect(Backend(), **kwargs)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert message in meta["configured_collection_error"]


@pytest.mark.parametrize("mode", ["partial", "sentinel"])
def test_partial_census_or_nonempty_exact_sentinel_fails_closed(mode: str) -> None:
    backend = Backend()
    if mode == "partial":
        backend.partial_start = 10
    else:
        backend.sentinel_payload = [deepcopy(backend.rows[-1])]
    rows, _, meta = _collect(backend)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["pagination_complete"] is False
    assert meta["detail_attempts"] == 0
    assert "expected 1 rows" in meta["configured_collection_error"] or "sentinel" in meta["configured_collection_error"]


@pytest.mark.parametrize("start", [1, 10])
def test_first_or_last_range_signature_change_fails_closed(start: int) -> None:
    backend = Backend()
    backend.mutate_edge_start = start
    rows, _, meta = _collect(backend)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["detail_attempts"] == 0
    assert "range signature changed" in meta["configured_collection_error"]


@pytest.mark.parametrize("fault", ["container", "title", "control"])
def test_any_current_detail_failure_fails_the_atomic_snapshot(fault: str) -> None:
    backend = Backend()
    backend.detail_faults["1001:1"] = fault
    rows, _, meta = _collect(backend)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["details_complete"] is False
    assert meta["detail_errors"] >= 1
    assert "course 1001:1" in meta["configured_collection_error"]


def test_transient_failure_retries_with_a_new_managed_session() -> None:
    backend = Backend()
    backend.fail_once[("GET", siheung.SIHEUNG_URL)] = 1
    rows, _, meta = _collect(backend)
    assert len(rows) == 2
    assert meta["snapshot_complete"] is True
    assert meta["retry_count"] == 1
    assert meta["physical_requests"] == 9
    assert meta["sessions_created"] == 2


def test_duplicate_identity_test_notice_and_semantic_duplicate_fail_closed() -> None:
    duplicate = Backend()
    duplicate.rows[-1]["d_sbjct_sn"] = duplicate.rows[-2]["d_sbjct_sn"]
    rows, _, meta = _collect(duplicate)
    assert rows == []
    assert meta["duplicate_count"] == 1

    notice = Backend()
    notice.rows[-1]["d_sbjct_nm"] = "테스트"
    rows, _, meta = _collect(notice)
    assert rows == []
    assert meta["test_or_notice_row_count"] == 1
    assert "test/notice" in meta["configured_collection_error"]

    semantic = Backend()
    for key in (
        "d_sbjct_nm",
        "d_edu_gvmnfc",
        "d_rgn",
        "d_edu_bgng_dt",
        "d_edu_end_dt",
        "d_edu_start_time",
        "d_edu_end_time",
    ):
        semantic.rows[1][key] = semantic.rows[0][key]
    semantic.venues["1001:1"] = "시흥시 동일 강의실"
    semantic.venues["1002:1"] = "시흥시 동일 강의실"
    rows, _, meta = _collect(semantic)
    assert rows == []
    assert meta["semantic_duplicate_count"] == 1


def test_owner_boundary_rejects_any_non_g000002_source_row() -> None:
    backend = Backend()
    backend.rows[-1]["d_co_sprvsn_id"] = "G000001"
    rows, _, meta = _collect(backend)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "non-Siheung site supervision" in meta["configured_collection_error"]
    assert meta["ownership_co_sponsor_id"] == "G000002"
    assert meta["parent_aggregate_exclusion_required"] is True


def test_only_exact_audited_source_test_row_is_excluded_without_detail_fetch() -> None:
    backend = Backend()
    backend.rows[-1] = _item(
        62076,
        status="모집중",
        start="2026.08.01",
        end="2026.08.27",
        title=siheung.SIHEUNG_EXCLUDED_TEST_ROWS[("62076", "1")],
        branch="시흥시체육회",
    )
    rows, _, meta = _collect(backend)

    assert len(rows) == 2
    assert meta["snapshot_complete"] is True
    assert meta["source_current_count"] == 3
    assert meta["current_count"] == meta["returned_count"] == 2
    assert meta["test_or_notice_row_count"] == 1
    assert meta["explicit_test_excluded_count"] == 1
    assert meta["current_explicit_test_excluded_count"] == 1
    assert all("s_sbjct_sn=62076" not in call[1] for call in backend.calls if call[0] == "GET")

    changed = Backend()
    changed.rows[-1] = deepcopy(backend.rows[-1])
    changed.rows[-1]["d_sbjct_nm"] += " 변경"
    rows, _, meta = _collect(changed)
    assert rows == []
    assert "audited test-row title changed" in meta["configured_collection_error"]


def test_managed_session_and_external_dedupe_are_fail_closed() -> None:
    rows, _, meta = siheung.collect_siheung_education_courses(Target())
    assert rows == []
    assert "managed session_factory" in meta["configured_collection_error"]

    rows, _, meta = siheung.collect_siheung_education_courses(
        Target(provider="WRONG"),
        session_factory=Backend().session_factory,
    )
    assert rows == []
    assert "canonical Siheung" in meta["configured_collection_error"]

    rows, _, meta = _collect(Backend(), dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed complete row count" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_SIHEUNG") != "1",
    reason="set RUN_LIVE_SIHEUNG=1 for the exact 2026-07-23 live audit",
)
def test_exact_live_siheung_snapshot_20260723() -> None:
    rows, parser, meta = siheung.collect_siheung_education_courses(
        Target(),
        timeout=40,
        max_pages=230,
        detail_limit=400,
        max_requests=750,
        today=date(2026, 7, 23),
        allow_raw_requests_for_tests=True,
    )

    assert parser == siheung.SIHEUNG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["source_total"] == meta["source_rows"] == 1788
    assert meta["eligible_source_rows"] == 1783
    assert meta["data_pages"] == 199
    assert meta["pages"] == 200
    assert meta["list_requests"] == 202
    assert meta["page_counts"] == {
        **{page: 9 for page in range(1, 199)},
        199: 6,
        200: 0,
    }
    assert meta["sentinel_start"] == 1789
    assert meta["sentinel_end"] == 1798
    assert meta["sentinel_count"] == 0
    assert meta["stability_rechecks"] == 2
    assert meta["first_identity"] == "61834:1"
    assert meta["last_identity"] == "50195:1"
    assert meta["first_page_signature"] == "23708cc4b1c9eff86fea71db71d19ba4b388ffae07842795ca40a0116d74451c"
    assert meta["last_page_signature"] == "10b9d1b16592e2161fb69d85da029af23c4b3e6b30927b7ac637bb6b479f81cf"
    assert meta["expired_count"] == 1477
    assert meta["source_current_count"] == 311
    assert meta["current_count"] == meta["returned_count"] == len(rows) == 310
    assert meta["detail_attempts"] == meta["detail_pages"] == 310
    assert meta["detail_errors"] == 0
    assert meta["source_status_counts"] == {
        "마감": 1738,
        "모집예정": 12,
        "대기접수": 4,
        "마감임박": 11,
        "모집중": 23,
    }
    assert meta["current_source_status_counts"] == {
        "마감": 261,
        "모집예정": 12,
        "대기접수": 4,
        "마감임박": 11,
        "모집중": 22,
    }
    assert meta["status_counts"] == {"CLOSED": 261, "SCHEDULED": 12, "OPEN": 37}
    assert meta["source_region_counts"] == {
        "거북섬동": 63, "군자동": 5, "능곡동": 17, "대야동": 751,
        "매화동": 8, "목감동": 90, "배곧1동": 144, "배곧2동": 68,
        "시흥시": 24, "신현동": 4, "연성동": 10, "월곶동": 18,
        "은행동": 94, "장곡동": 45, "정왕1동": 338, "정왕2동": 53,
        "정왕3동": 10, "정왕4동": 32, "정왕본동": 14,
    }
    assert meta["current_source_region_counts"] == {
        "거북섬동": 19, "군자동": 1, "능곡동": 15, "대야동": 79,
        "매화동": 2, "목감동": 21, "배곧1동": 31, "배곧2동": 26,
        "월곶동": 14, "은행동": 12, "장곡동": 12, "정왕1동": 49,
        "정왕2동": 10, "정왕3동": 1, "정왕4동": 15, "정왕본동": 3,
    }
    assert meta["branch_counts"] == {
        "거북섬동 주민자치회": 19, "능곡동 주민자치회": 15,
        "대야동 주민자치회": 13, "매화동 주민자치회": 1,
        "목감동 주민자치회": 11, "배곧1동 주민자치회": 27,
        "배곧2동 주민자치회": 25, "시흥시 건강돌봄과": 1,
        "시흥시 건강증진과": 4, "시흥시 교육자치과": 3,
        "시흥시 대야평생학습관": 40, "시흥시 아동돌봄과": 8,
        "시흥시 일자리경제과": 1, "시흥시 정왕보건지소": 2,
        "시흥시 정왕평생학습관": 41, "시흥시 질병관리과": 6,
        "시흥시 질병관리과_": 3, "시흥시 평생학습과": 23,
        "시흥시체육회": 25, "월곶동 주민자치회": 14,
        "은행동 주민자치회": 8, "장곡동 주민자치회": 10,
        "정왕2동 주민자치회": 10,
    }
    assert meta["branch_count"] == 23
    assert meta["venue_count"] == 103
    assert meta["duplicate_count"] == 0
    assert meta["duplicate_url_count"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert meta["test_or_notice_row_count"] == 5
    assert meta["explicit_test_excluded_count"] == 5
    assert meta["current_explicit_test_excluded_count"] == 1
    assert meta["pii_redaction_count"] == 32
    assert meta["application_control_count"] == 310
    assert meta["reservation_discovery_links"] == 37
    assert meta["required_logical_requests"] == 513
    assert meta["physical_requests"] == 513
    assert meta["retry_count"] == 0
    assert meta["sessions_created"] == 6
    assert meta["application_endpoints_called"] == 0
    assert all(row["provider"] == siheung.SIHEUNG_PROVIDER for row in rows)
    assert all(row["branch"] in siheung.SIHEUNG_AUDITED_BRANCHES for row in rows)
    assert all(row["region"] == siheung.SIHEUNG_MUNICIPALITY_NAME for row in rows)
    assert all(row.get("venue_name") for row in rows)
    assert sum(bool(row.get("application_url")) for row in rows) == 37


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_SIHEUNG") != "1",
    reason="set RUN_LIVE_SIHEUNG=1 for the parent-owner overlap audit",
)
def test_exact_live_parent_aggregate_owner_boundary_20260723() -> None:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "X-Requested-With": "XMLHttpRequest",
        }
    )

    def census(url: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        total = 0
        page = 1
        while not total or len(result) < total:
            start, end = siheung.siheung_api_range(page)
            data = {
                "s_sort_by": "1",
                "s_row_start": str(start),
                "s_row_end": str(end),
                "resion": siheung.SIHEUNG_REGION_CODE,
            }
            response = session.post(
                url,
                data=data,
                headers={"Referer": url.removesuffix("/search")},
                timeout=40,
                allow_redirects=False,
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload
            if not total:
                total = int(payload[0]["d_total_cnt"])
            assert {int(item["d_total_cnt"]) for item in payload} == {total}
            result.extend(payload)
            page += 1
        return result

    branded = census(siheung.SIHEUNG_API_URL)
    parent_filtered = census(siheung.SIHEUNG_PARENT_API_URL)

    def identity(item: dict[str, Any]) -> tuple[str, str]:
        return (
            str(item.get("d_sbjct_sn")),
            str(item.get("d_sbjct_cycl_sn")),
        )

    branded_ids = {identity(item) for item in branded}
    filtered_ids = {identity(item) for item in parent_filtered}
    assert len(branded) == len(branded_ids) == 1788
    assert len(parent_filtered) == len(filtered_ids) == 1786
    assert len(branded_ids & filtered_ids) == 1783
    assert len(branded_ids - filtered_ids) == 5
    assert len(filtered_ids - branded_ids) == 3
    assert all(item["d_co_sprvsn_id"] == siheung.SIHEUNG_CO_SPONSOR_ID for item in parent_filtered)
    assert {item["d_total_cnt"] for item in parent_filtered} == {"1786"}
    assert {item["d_rgn"] for item in parent_filtered} == {"시흥시"}
    current_branded = {
        identity(item) for item in branded if item["d_edu_end_dt"] >= "2026.07.23"
    }
    current_parent = {
        identity(item) for item in parent_filtered if item["d_edu_end_dt"] >= "2026.07.23"
    }
    assert len(current_branded) == len(current_parent) == 311
    assert current_branded == current_parent
