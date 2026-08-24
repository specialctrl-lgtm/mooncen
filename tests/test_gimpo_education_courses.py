from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from Crawler import municipal_gimpo as gimpo


@dataclass
class Target:
    provider: str = gimpo.GIMPO_PROVIDER
    name: str = "김포시 평생교육 통합 플랫폼"
    branch: str = "경기도 김포시"
    url: str = gimpo.GIMPO_URL


class FakeResponse:
    def __init__(
        self,
        *,
        text: str = "",
        payload: Any = None,
        status_code: int = 200,
        history: list[Any] | None = None,
    ) -> None:
        self.content = text.encode("utf-8")
        self.text = text
        self._payload = payload
        self.status_code = status_code
        self.history = history or []

    def json(self) -> Any:
        return self._payload


class FakeSession:
    def __init__(
        self,
        landing: str,
        ranges: dict[int, list[Any]],
        details: dict[str, str],
    ) -> None:
        self.landing = landing
        self.ranges = ranges
        self.details = details
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, Any]] = []
        self.closed = False

    def get(
        self,
        url: str,
        *,
        timeout: int,
        allow_redirects: bool,
    ) -> FakeResponse:
        assert timeout == 7
        assert allow_redirects is False
        self.calls.append(("GET", url))
        if url == gimpo.GIMPO_URL:
            return FakeResponse(text=self.landing)
        return FakeResponse(text=self.details[url])

    def post(
        self,
        url: str,
        *,
        data: dict[str, str],
        timeout: int,
        allow_redirects: bool,
        headers: dict[str, str],
    ) -> FakeResponse:
        assert url == gimpo.GIMPO_API_URL
        assert timeout == 7
        assert allow_redirects is False
        assert data["resion"] == gimpo.GIMPO_REGION_CODE
        assert headers["Referer"] == gimpo.GIMPO_URL
        start = int(data["s_row_start"])
        self.calls.append(("POST", start))
        return FakeResponse(payload=self.ranges[start])

    def close(self) -> None:
        self.closed = True


def _landing(total: int, *, region: str = gimpo.GIMPO_REGION_CODE) -> str:
    return f"""
    <html><head><title>김포시 평생교육 통합 플랫폼</title></head><body>
      <input id="s_resion_cd1" name="s_resion_cd1" value="{region}">
      <input name="ARK_CO_SPRVSN_ID" value="{gimpo.GIMPO_PRIMARY_CO_SPONSOR_ID}">
      <main><p>총 {total:,} 개의 강좌가 있습니다.</p></main>
      <footer>COPYRIGHT © GIMPO CITY.</footer>
    </body></html>
    """


def _item(
    subject: str,
    *,
    total: int = 2,
    cycle: str = "1",
    title: str = "AI 시민교실",
    branch: str = "김포시 평생학습관",
    status: str = "모집중",
    start: str = "2099.07.01",
    end: str = "2099.08.01",
    registered: str = "20990601120000",
    sponsor: str = gimpo.GIMPO_PRIMARY_CO_SPONSOR_ID,
    region: str = "김포시",
    single_day: bool = False,
) -> dict[str, Any]:
    return {
        "d_total_cnt": str(total),
        "d_sbjct_sn": subject,
        "d_sbjct_cycl_sn": cycle,
        "d_sbjct_nm": title,
        "d_edu_gvmnfc": branch,
        "d_rgn": region,
        "d_co_sprvsn_id": sponsor,
        "d_sbjct_type_cd_id": "OF",
        "d_recrut_stts_nm": status,
        "d_edu_bgng_dt": start,
        "d_edu_end_dt": end,
        "d_is_single_day_course": "Y" if single_day else "N",
        "d_edu_wday_cd_nm": "화",
        "d_edu_start_time": "10:00",
        "d_edu_end_time": "12:00",
        "d_edu_nope": "20",
        "d_aply_cnt": "3",
        "d_sbjct_amt": "10000",
        "d_prepar_cmdty_amt": "0",
        "d_stdnt_chice_mthd_cd_nm": "선착",
        "d_sbjct_trgt_nm_1": "성인",
        "d_sbjct_intrd_cn": "김포 시민을 위한 수업",
        "d_instr_nm": "김강사",
        "d_clsf_depth1_nm": "디지털역량",
        "d_clsf_depth2_nm": "AI",
        "d_clsf_depth3_nm": "기초",
        "d_reg_dt": registered,
    }


def _detail(
    subject: str,
    *,
    cycle: str = "1",
    title: str = "AI 시민교실",
    branch: str = "김포시 평생학습관",
    status: str = "모집중",
    disabled: bool = False,
    start: str = "2099.07.01",
    end: str = "2099.08.01",
    venue: str = "경기 김포시 김포한강1로 242 김포시 평생학습관",
    single_day: bool = False,
) -> str:
    detail_url = gimpo.gimpo_detail_url(subject, cycle)
    parsed = urlparse(detail_url)
    disabled_class = " disabled" if disabled else ""
    learning_period = (
        f"<dt>학습일자</dt><dd>{start}.(화)</dd>"
        if single_day
        else f"<dt>학습기간</dt><dd>{start}.(화) ~ {end}.(금)</dd>"
    )
    return f"""
    <html><body>
      <div class="course-detail-container">
        <section class="key-course-info">
          <span class="tag-item-xs">{status}</span>
          <span class="tag-field">{branch}</span>
          <h2 class="course-title">{title}</h2>
          <p class="course-desc">김포 시민을 위한 상세 수업입니다.</p>
          <dl>
            <dt>신청기간</dt><dd>2099.06.01.(월) ~ 2099.06.30.(화)</dd>
            {learning_period}
            <dt>교육시간</dt><dd>매주 화 10:00 ~ 12:00</dd>
          </dl>
          <dl>
            <dt>교육대상</dt><dd>성인</dd>
            <dt>모집인원</dt><dd>총 20 명</dd>
            <dt>수강료</dt><dd>10,000원</dd>
            <dt>교육장소</dt><dd>{venue}</dd>
          </dl>
        </section>
        <div class="btn-course-box">
          <a class="btn-course-apply{disabled_class}">{status}</a>
        </div>
      </div>
      <form id="form1">
        <input name="s_sbjct_sn" value="{subject}">
        <input name="s_sbjct_cycl_sn" value="{cycle}">
      </form>
      <form id="loginForm">
        <input name="p_return_url" value="{parsed.path}?{parsed.query}">
      </form>
      <script>
        const cert = "/user/course/cert/checkCi";
        function moveToAplyPage() {{ document.getElementById("form1").action = "/user/course/aply"; }}
      </script>
    </body></html>
    """


def _fixture(
    *,
    current_item: dict[str, Any] | None = None,
    current_detail: str | None = None,
) -> tuple[FakeSession, dict[str, Any], dict[str, Any]]:
    current = current_item or _item("101")
    expired = _item(
        "99",
        title="종료 강좌",
        status="마감",
        start="2020.01.01",
        end="2020.02.01",
        registered="20200101010101",
    )
    ranges = {1: [current, expired], 3: []}
    detail_url = gimpo.gimpo_detail_url(
        current["d_sbjct_sn"], current["d_sbjct_cycl_sn"]
    )
    details = {
        detail_url: current_detail
        or _detail(
            current["d_sbjct_sn"],
            cycle=current["d_sbjct_cycl_sn"],
            title=current["d_sbjct_nm"],
            branch=current["d_edu_gvmnfc"],
            status=current["d_recrut_stts_nm"],
            disabled=current["d_recrut_stts_nm"] not in {"모집중", "마감임박", "대기접수", "추가접수"},
            start=current["d_edu_bgng_dt"],
            end=current["d_edu_end_dt"],
        )
    }
    return FakeSession(_landing(2), ranges, details), current, expired


def _collect(session: FakeSession, **kwargs: Any):
    return gimpo.collect_gimpo_education_courses(
        Target(),
        timeout=7,
        max_pages=kwargs.pop("max_pages", 2),
        detail_limit=kwargs.pop("detail_limit", 10),
        session_factory=lambda: session,
        today=kwargs.pop("today", "2099-06-15"),
        **kwargs,
    )


def test_collects_complete_current_snapshot_and_immediate_sentinel() -> None:
    session, _current, _expired = _fixture()

    rows, parser, meta = _collect(session)

    assert parser == gimpo.GIMPO_PARSER
    assert len(rows) == 1
    row = rows[0]
    assert row["provider_course_id"] == f"{gimpo.GIMPO_PROVIDER}:course:101:1"
    assert row["title"] == "AI 시민교실"
    assert row["branch"] == "김포시 평생학습관"
    assert row["status"] == "OPEN"
    assert row["period"] == "2099-07-01 ~ 2099-08-01"
    assert row["apply_period"] == "2099-06-01 ~ 2099-06-30"
    assert row["capacity_total"] == 20
    assert row["capacity_current"] == 3
    assert row["reservation_available"] is True
    assert row["application_url"] == row["raw_url"]
    assert parse_qs(urlparse(row["raw_url"]).query) == {
        "s_sbjct_sn": ["101"],
        "s_sbjct_cycl_sn": ["1"],
    }
    assert meta["source_total"] == 2
    assert meta["source_rows"] == 2
    assert meta["data_pages"] == 1
    assert meta["sentinel_page"] == 2
    assert meta["sentinel_start"] == 3
    assert meta["page_counts"] == {1: 2, 2: 0}
    assert meta["expired_count"] == 1
    assert meta["current_count"] == 1
    assert meta["detail_pages"] == 1
    assert meta["snapshot_complete"] is True
    assert ("POST", 3) in session.calls
    assert session.closed is True


def test_resident_aggregate_is_split_to_audited_real_branch() -> None:
    item = _item(
        "101",
        title="[김포본동 단기특강] 배워두면 평생 쓰는 바레 기초 특강",
        branch=gimpo.GIMPO_AGGREGATE_RESIDENT_BRANCH,
        region="김포본동",
    )
    detail = _detail(
        "101",
        title=item["d_sbjct_nm"],
        branch=gimpo.GIMPO_AGGREGATE_RESIDENT_BRANCH,
        venue="경기 김포시 북변1로 13 김포본동행정복지센터 2층 강의실2",
    )
    session, _current, _expired = _fixture(
        current_item=item,
        current_detail=detail,
    )

    rows, _parser, meta = _collect(session)

    assert meta["snapshot_complete"] is True
    assert rows[0]["branch"] == "김포본동 가까이배움터"
    assert rows[0]["provider_organizer"] == "읍면동 가까이배움터"
    assert "김포본동행정복지센터" in rows[0]["venue_name"]


def test_unknown_aggregate_prefix_and_ownership_are_fail_closed() -> None:
    item = _item("101", title="[가짜동 단기특강] 테스트", branch=gimpo.GIMPO_AGGREGATE_RESIDENT_BRANCH)
    session, _current, _expired = _fixture(current_item=item)

    rows, _parser, meta = _collect(session)

    assert rows == []
    assert "no audited title prefix" in meta["configured_collection_error"]

    session2, current2, _expired2 = _fixture()
    current2["d_rgn"] = "고양시"
    current2["d_co_sprvsn_id"] = "OTHER"
    rows, _parser, meta = _collect(session2)
    assert rows == []
    assert "non-Gimpo local region" in meta["configured_collection_error"]
    assert "unaudited Gimpo co-sponsor" in meta["configured_collection_error"]


def test_central_gseek_shared_sponsor_rows_are_not_owned() -> None:
    ended = _item(
        "99",
        total=1,
        title="평생배움대학 종료 과정",
        branch="평생배움대학",
        status="마감",
        start="2020.01.01",
        end="2020.02.01",
        sponsor=gimpo.GIMPO_EXCLUDED_SHARED_CO_SPONSOR_ID,
        registered="20200101010101",
    )
    session = FakeSession(_landing(1), {1: [ended], 2: []}, {})
    rows, _parser, meta = _collect(session)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "unaudited Gimpo co-sponsor" in meta["configured_collection_error"]


def test_page_and_detail_caps_are_fail_closed() -> None:
    session, _current, _expired = _fixture()
    rows, _parser, meta = _collect(session, max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "1 of 2 required API range requests" in meta["configured_collection_error"]
    assert not any(call[0] == "POST" for call in session.calls)

    session2, _current2, _expired2 = _fixture()
    rows, _parser, meta = _collect(session2, detail_limit=0)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "0 of 1 required current/future details" in meta["configured_collection_error"]
    assert not any(call[0] == "GET" and call[1] != gimpo.GIMPO_URL for call in session2.calls)


def test_range_total_sentinel_and_identity_contracts_are_fail_closed() -> None:
    session, current, _expired = _fixture()
    session.ranges[1] = [current]
    rows, _parser, meta = _collect(session)
    assert rows == []
    assert "expected 2 rows, got 1" in meta["configured_collection_error"]
    assert "declared total 2 != parsed rows 1" in meta["configured_collection_error"]

    session2, current2, _expired2 = _fixture()
    session2.ranges[3] = [dict(current2)]
    rows, _parser, meta = _collect(session2)
    assert rows == []
    assert "immediate post-total sentinel" in meta["configured_collection_error"]

    session3, current3, _expired3 = _fixture()
    duplicate = dict(current3)
    session3.ranges[1] = [current3, duplicate]
    rows, _parser, meta = _collect(session3)
    assert rows == []
    assert meta["duplicate_count"] == 1


def test_reopened_identical_application_round_keeps_latest_registration() -> None:
    old = _item(
        "101",
        total=2,
        status="마감",
        registered="20990501120000",
    )
    latest = _item(
        "102",
        total=2,
        status="모집중",
        registered="20990601120000",
    )
    ranges = {1: [latest, old], 3: []}
    details = {
        gimpo.gimpo_detail_url("101", "1"): _detail("101", status="마감", disabled=True),
        gimpo.gimpo_detail_url("102", "1"): _detail("102", status="모집중"),
    }
    session = FakeSession(_landing(2), ranges, details)

    rows, _parser, meta = _collect(session)

    assert meta["snapshot_complete"] is True
    assert meta["current_count"] == 2
    assert meta["detail_pages"] == 2
    assert meta["semantic_duplicate_count"] == 1
    assert meta["duplicate_rounds_removed"] == 1
    assert len(rows) == 1
    assert rows[0]["provider_course_id"].endswith(":102:1")
    assert rows[0]["raw_fields"]["reopened_duplicate_round_ids"] == [
        f"{gimpo.GIMPO_PROVIDER}:course:101:1"
    ]


def test_detail_contract_and_external_dedupe_are_fail_closed() -> None:
    session, current, _expired = _fixture()
    url = gimpo.gimpo_detail_url("101", "1")
    session.details[url] = _detail(
        "999",
        title="다른 강좌",
        branch=current["d_edu_gvmnfc"],
        status=current["d_recrut_stts_nm"],
        disabled=True,
    )
    rows, _parser, meta = _collect(session)
    assert rows == []
    assert "detail identity mismatch" in meta["configured_collection_error"]
    assert "detail/list title mismatch" in meta["configured_collection_error"]
    assert "status/application control mismatch" in meta["configured_collection_error"]

    session2, _current2, _expired2 = _fixture()
    rows, _parser, meta = _collect(session2, dedupe_rows=lambda _rows: [])
    assert rows == []
    assert "dedupe changed complete row count 1 to 0" in meta["configured_collection_error"]


def test_scheduled_course_keeps_detail_url_but_clears_application_url() -> None:
    scheduled = _item("101", status="모집예정")
    session, _current, _expired = _fixture(current_item=scheduled)

    rows, _parser, meta = _collect(session)

    assert meta["snapshot_complete"] is True
    assert rows[0]["status"] == "SCHEDULED"
    assert rows[0]["reservation_available"] is False
    assert "application_url" not in rows[0]
    assert rows[0]["raw_url"].startswith("https://gimpo.gseek.kr/")


def test_single_day_learning_date_contract_is_supported() -> None:
    item = _item(
        "101",
        start="2099.08.03",
        end="2099.08.03",
        single_day=True,
    )
    detail = _detail(
        "101",
        start="2099.08.03",
        end="2099.08.03",
        single_day=True,
    )
    session, _current, _expired = _fixture(current_item=item, current_detail=detail)

    rows, _parser, meta = _collect(session)

    assert meta["snapshot_complete"] is True
    assert rows[0]["period"] == "2099-08-03 ~ 2099-08-03"


def test_sessions_rotate_below_managed_request_budget(monkeypatch: Any) -> None:
    monkeypatch.setattr(gimpo, "GIMPO_SESSION_REQUEST_LIMIT", 2)
    base, _current, _expired = _fixture()
    sessions = [
        FakeSession(base.landing, base.ranges, base.details),
        FakeSession(base.landing, base.ranges, base.details),
    ]
    created: list[FakeSession] = []

    def factory() -> FakeSession:
        session = sessions[len(created)]
        created.append(session)
        return session

    rows, _parser, meta = gimpo.collect_gimpo_education_courses(
        Target(),
        timeout=7,
        max_pages=2,
        detail_limit=10,
        session_factory=factory,
        today="2099-06-15",
    )

    assert len(rows) == 1
    assert meta["snapshot_complete"] is True
    assert meta["sessions_created"] == 2
    assert len(created) == 2
    assert all(session.closed for session in created)


def test_target_url_helpers_and_managed_session_requirement_are_strict() -> None:
    assert gimpo.is_gimpo_education_target(Target()) is True
    assert gimpo.is_gimpo_education_target(Target(provider="WRONG")) is False
    assert gimpo.is_gimpo_education_target(Target(url=gimpo.GIMPO_URL + "?extra=1")) is False
    assert gimpo.gimpo_api_range("1") == (1, 10)
    assert gimpo.gimpo_api_range("2") == (10, 19)
    assert gimpo.gimpo_api_range("../2") == (0, 0)
    assert gimpo.gimpo_sentinel_range("517") == (518, 527)
    assert gimpo.gimpo_detail_url("101", "2").endswith(
        "s_sbjct_sn=101&s_sbjct_cycl_sn=2"
    )
    assert gimpo.gimpo_detail_url("101&evil=1", "2") == ""

    rows, parser, meta = gimpo.collect_gimpo_education_courses(Target())
    assert rows == []
    assert parser == gimpo.GIMPO_PARSER
    assert meta["snapshot_complete"] is False
    assert "managed session_factory" in meta["configured_collection_error"]


def test_landing_region_contract_is_fail_closed() -> None:
    session, _current, _expired = _fixture()
    session.landing = _landing(2, region="WRONG")
    rows, _parser, meta = _collect(session)
    assert rows == []
    assert "catalogue contract" in meta["configured_collection_error"]
