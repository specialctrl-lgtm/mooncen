from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from Crawler import municipal_guri as guri


@dataclass
class Target:
    provider: str = guri.GURI_GSEEK_PROVIDER
    name: str = "구리시 평생학습포털"
    branch: str = "경기도 구리시"
    url: str = guri.GURI_GSEEK_URL


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
        pages: dict[int, list[Any]],
        details: dict[str, str],
    ) -> None:
        self.landing = landing
        self.pages = pages
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
        if url == guri.GURI_GSEEK_URL:
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
        assert url == guri.GURI_GSEEK_API_URL
        assert timeout == 7
        assert allow_redirects is False
        assert data["resion"] == guri.GURI_GSEEK_REGION_CODE
        assert headers["Referer"] == guri.GURI_GSEEK_URL
        start = int(data["s_row_start"])
        page = ((start - 1) // guri.GURI_GSEEK_PAGE_SIZE) + 1
        self.calls.append(("POST", page))
        return FakeResponse(payload=self.pages[page])

    def close(self) -> None:
        self.closed = True


def _landing(total: int) -> str:
    return f"""
    <html><head><title>구리시 평생학습포털</title></head><body>
      <main><p>총 {total:,} 개의 강좌가 있습니다.</p></main>
      <footer>COPYRIGHT © GURI CITY.</footer>
    </body></html>
    """


def _item(
    subject: str,
    *,
    total: int = 2,
    cycle: str = "1",
    title: str = "AI 시민교실",
    branch: str = "구리시 평생학습관",
    status: str = "모집중",
    start: str = "2099.07.01",
    end: str = "2099.08.01",
) -> dict[str, Any]:
    return {
        "d_total_cnt": str(total),
        "d_sbjct_sn": subject,
        "d_sbjct_cycl_sn": cycle,
        "d_sbjct_nm": title,
        "d_edu_gvmnfc": branch,
        "d_rgn": "구리시",
        "d_co_sprvsn_id": guri.GURI_GSEEK_CO_SPONSOR_ID,
        "d_sbjct_type_cd_id": "OF",
        "d_recrut_stts_nm": status,
        "d_edu_bgng_dt": start,
        "d_edu_end_dt": end,
        "d_edu_wday_cd_nm": "화",
        "d_edu_start_time": "10:00",
        "d_edu_end_time": "12:00",
        "d_edu_nope": "20",
        "d_aply_cnt": "3",
        "d_sbjct_amt": "10000",
        "d_prepar_cmdty_amt": "0",
        "d_stdnt_chice_mthd_cd_nm": "선착",
        "d_sbjct_trgt_nm_1": "성인",
        "d_sbjct_intrd_cn": "구리 시민을 위한 수업",
        "d_clsf_depth1_nm": "디지털역량",
        "d_clsf_depth2_nm": "AI",
        "d_clsf_depth3_nm": "기초",
    }


def _detail(
    subject: str,
    *,
    cycle: str = "1",
    title: str = "AI 시민교실",
    branch: str = "구리시 평생학습관",
    status: str = "모집중",
    disabled: bool = False,
    start: str = "2099.07.01",
    end: str = "2099.08.01",
) -> str:
    detail_url = guri.guri_gseek_detail_url(subject, cycle)
    parsed = urlparse(detail_url)
    disabled_class = " disabled" if disabled else ""
    return f"""
    <html><body>
      <div class="course-detail-container">
        <section class="key-course-info">
          <span class="tag-item-xs">{status}</span>
          <span class="tag-field">{branch}</span>
          <h2 class="course-title">{title}</h2>
          <p class="course-desc">구리 시민을 위한 상세 수업입니다.</p>
          <dl>
            <dt>신청기간</dt><dd>2099.06.01.(월) ~ 2099.06.30.(화)</dd>
            <dt>학습기간</dt><dd>{start}.(화) ~ {end}.(금)</dd>
            <dt>교육시간</dt><dd>매주 화 10:00 ~ 12:00</dd>
          </dl>
          <dl>
            <dt>교육대상</dt><dd>성인</dd>
            <dt>모집인원</dt><dd>총 20 명</dd>
            <dt>수강료</dt><dd>10,000원</dd>
            <dt>교육장소</dt><dd>구리시 평생학습관 1층</dd>
          </dl>
        </section>
        <div class="btn-course-box">
          <span class="btn-course-apply{disabled_class}">{status}</span>
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
        function moveToAplyPage() {{
          document.getElementById("form1").action = "/user/course/aply";
        }}
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
    )
    pages = {1: [current, expired], 2: []}
    detail_url = guri.guri_gseek_detail_url(
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
    return FakeSession(_landing(2), pages, details), current, expired


def _collect(session: FakeSession, **kwargs: Any):
    return guri.collect_guri_gseek_courses(
        Target(),
        timeout=7,
        max_pages=kwargs.pop("max_pages", 2),
        detail_limit=kwargs.pop("detail_limit", 10),
        session_factory=lambda: session,
        today=kwargs.pop("today", "2099-06-15"),
        **kwargs,
    )


def test_collects_complete_current_snapshot_and_empty_sentinel() -> None:
    session, _current, _expired = _fixture()

    rows, parser, meta = _collect(session)

    assert parser == guri.GURI_GSEEK_PARSER
    assert len(rows) == 1
    row = rows[0]
    assert row["provider_course_id"] == f"{guri.GURI_GSEEK_PROVIDER}:course:101:1"
    assert row["title"] == "AI 시민교실"
    assert row["branch"] == "구리시 평생학습관"
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
    assert meta["page_counts"] == {1: 2, 2: 0}
    assert meta["expired_count"] == 1
    assert meta["current_count"] == 1
    assert meta["detail_pages"] == 1
    assert meta["branch_counts"] == {"구리시 평생학습관": 1}
    assert meta["snapshot_complete"] is True
    assert session.closed is True


def test_complete_ended_catalogue_does_not_fetch_details() -> None:
    ended = _item(
        "99",
        total=1,
        title="종료 강좌",
        status="마감",
        start="2020.01.01",
        end="2020.02.01",
    )
    session = FakeSession(_landing(1), {1: [ended], 2: []}, {})

    rows, _parser, meta = _collect(session)

    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["detail_pages"] == 0
    assert all(call[0] != "GET" or call[1] == guri.GURI_GSEEK_URL for call in session.calls)


def test_max_pages_must_cover_data_ranges_and_sentinel() -> None:
    session, _current, _expired = _fixture()

    rows, _parser, meta = _collect(session, max_pages=1)

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert "1 of 2 required API range requests" in meta["configured_collection_error"]
    assert not any(call[0] == "POST" for call in session.calls)


def test_range_size_and_catalogue_total_are_fail_closed() -> None:
    session, current, expired = _fixture()
    session.pages[1] = [current]

    rows, _parser, meta = _collect(session)

    assert rows == []
    assert "expected 2 rows, got 1" in meta["configured_collection_error"]
    assert "declared total 2 != parsed rows 1" in meta["configured_collection_error"]

    session2, current2, _expired2 = _fixture()
    current2["d_total_cnt"] = "3"
    rows, _parser, meta = _collect(session2)
    assert rows == []
    assert "catalogue total changed" in meta["configured_collection_error"]


def test_nonempty_sentinel_and_duplicate_identity_are_fail_closed() -> None:
    session, current, _expired = _fixture()
    sentinel = dict(current)
    session.pages[2] = [sentinel]

    rows, _parser, meta = _collect(session)

    assert rows == []
    assert "sentinel range" in meta["configured_collection_error"]

    session2, current2, _expired2 = _fixture()
    duplicate = dict(current2)
    duplicate["d_total_cnt"] = "2"
    session2.pages[1] = [current2, duplicate]
    rows, _parser, meta = _collect(session2)
    assert rows == []
    assert meta["duplicate_count"] == 1
    assert "duplicate provider course identities" in meta["configured_collection_error"]


def test_guri_ownership_contract_is_required() -> None:
    session, current, _expired = _fixture()
    current["d_rgn"] = "남양주시"
    current["d_co_sprvsn_id"] = "OTHER"

    rows, _parser, meta = _collect(session)

    assert rows == []
    assert "non-Guri region" in meta["configured_collection_error"]
    assert "non-Guri co-sponsor" in meta["configured_collection_error"]


def test_detail_identity_title_and_application_controls_are_fail_closed() -> None:
    session, current, _expired = _fixture()
    url = guri.guri_gseek_detail_url("101", "1")
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
    assert meta["detail_errors"] >= 3


def test_detail_limit_and_external_dedupe_are_fail_closed() -> None:
    session, _current, _expired = _fixture()

    rows, _parser, meta = _collect(session, detail_limit=0)

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "0 of 1 required current/future details" in meta["configured_collection_error"]

    session2, _current2, _expired2 = _fixture()
    rows, _parser, meta = _collect(session2, dedupe_rows=lambda _rows: [])
    assert rows == []
    assert "dedupe changed complete row count 1 to 0" in meta["configured_collection_error"]


def test_scheduled_course_keeps_detail_url_but_clears_application_url() -> None:
    scheduled = _item("101", status="모집예정")
    session, _current, _expired = _fixture(current_item=scheduled)

    rows, _parser, meta = _collect(session)

    assert meta["snapshot_complete"] is True
    assert len(rows) == 1
    assert rows[0]["status"] == "SCHEDULED"
    assert rows[0]["reservation_available"] is False
    assert "application_url" not in rows[0]
    assert rows[0]["raw_url"].startswith("https://guri.gseek.kr/")


def test_target_legacy_alias_and_url_helpers_are_strict() -> None:
    assert guri.is_guri_gseek_target(Target()) is True
    assert guri.is_guri_gseek_target(Target(provider="WRONG")) is False
    assert guri.is_guri_gseek_target(Target(url=guri.GURI_GSEEK_URL + "?extra=1")) is False
    assert guri.guri_gseek_api_range("1") == (1, 10)
    assert guri.guri_gseek_api_range("2") == (10, 19)
    assert guri.guri_gseek_api_range("../2") == (0, 0)
    assert guri.guri_gseek_detail_url("101", "2").endswith(
        "s_sbjct_sn=101&s_sbjct_cycl_sn=2"
    )
    assert guri.guri_gseek_detail_url("101&evil=1", "2") == ""
    assert guri.is_guri_legacy_redirect_target(
        Target(
            provider=guri.GURI_LEGACY_RESERVE_PROVIDER,
            url=guri.GURI_LEGACY_RESERVE_URL,
        )
    ) is True


def test_managed_session_injection_is_required() -> None:
    rows, parser, meta = guri.collect_guri_gseek_courses(Target())

    assert rows == []
    assert parser == guri.GURI_GSEEK_PARSER
    assert meta["snapshot_complete"] is False
    assert "managed session_factory" in meta["configured_collection_error"]


class FakeReserveSession:
    def __init__(
        self,
        *,
        active_row: bool = True,
        bad_inventory: bool = False,
        nonempty_sentinel: bool = False,
        detail_title: str = "주민 디지털교실",
    ) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []
        self.closed = False
        self.active_row = active_row
        self.bad_inventory = bad_inventory
        self.nonempty_sentinel = nonempty_sentinel
        self.detail_title = detail_title

    @staticmethod
    def _anchor(source: guri.GuriReserveSource) -> str:
        return (
            '<a class="tab_anchor" href="'
            + guri.guri_reserve_list_url(source, 1).replace(
                "https://www.guri.go.kr", ""
            )
            + f'">{source.name}</a>'
        )

    def _inventory(self) -> str:
        # The real retired lifelong page lists one resident representative and
        # the nine other top-level catalogues.
        top_level = [guri.GURI_RESERVE_SOURCES[0], *guri.GURI_RESERVE_SOURCES[8:]]
        if self.bad_inventory:
            top_level = top_level[:-1]
        return "<html><body>" + "".join(self._anchor(source) for source in top_level) + "</body></html>"

    def _resident_inventory(self) -> str:
        return "".join(
            self._anchor(source) for source in guri.GURI_RESERVE_SOURCES[:8]
        )

    @staticmethod
    def _course_row(source: guri.GuriReserveSource) -> str:
        education_key = (
            f"&amp;searchEduKey={source.education_key}"
            if source.education_key
            else "&amp;searchEduKey="
        )
        return f"""
        <tr>
          <td>1</td>
          <td><span>{source.name}</span><br>
            <a class="subject" href="./selectGuriUserCourseView.do?key={source.menu_key}{education_key}&amp;searchEduInstSe={source.institution_code}&amp;lctreRcritKey=7001">주민 디지털교실</a>
          </td>
          <td><span class="acc_date">2099-06-01~2099-06-30</span><span class="edu_date">2099-07-01~2099-08-01</span></td>
          <td>화 / 10:00 ~ 12:00</td><td>선착순</td>
          <td>신청 3명 / 모집정원 20 명 (대기신청 0 명/ 대기정원 2 명)</td>
          <td>인터넷</td><td><span class="acc_btn">접수중</span></td>
        </tr>
        """

    def _list_page(
        self,
        source: guri.GuriReserveSource,
        page: int,
    ) -> str:
        is_active_source = source == guri.GURI_RESERVE_SOURCES[0] and self.active_row
        total = 1 if is_active_source else 0
        rows = ""
        if is_active_source and (page == 1 or (page == 2 and self.nonempty_sentinel)):
            rows = self._course_row(source)
        if not rows:
            rows = '<tr><td colspan="8">등록된 강좌가 없습니다.</td></tr>'
        inventory = self._resident_inventory() if source == guri.GURI_RESERVE_SOURCES[0] else ""
        return f"""
        <html><body>{inventory}
          <p>총게시물 : {total} 건 페이지 : {page}/1</p>
          <table><tbody>{rows}</tbody></table>
        </body></html>
        """

    def _detail(self, source: guri.GuriReserveSource) -> str:
        return f"""
        <html><body>
          <div class="title_area"><strong class="tit_text">{self.detail_title}</strong><span class="acc_btn">접수중</span></div>
          <table><tbody>
            <tr><th>접수기간</th><td>2099-06-01 09:00~2099-06-30 18:00</td></tr>
            <tr><th>접수현황</th><td>신청 3명 / 모집정원 20 명 (대기신청 0 명/ 대기정원 2 명)</td></tr>
            <tr><th>선발방법</th><td>선착순</td><th>신청방법</th><td>인터넷</td></tr>
            <tr><th>교육대상</th><td>성인</td><th>교육기간</th><td>2099-07-01~2099-08-01</td></tr>
            <tr><th>교육시간</th><td>화 / 10:00 ~ 12:00</td><th>교육장</th><td>갈매동 행정복지센터</td></tr>
            <tr><th>수강료</th><td>0</td><th>문의전화</th><td>031-550-0000</td></tr>
            <tr><th>강의소개</th><td>주민 디지털교실입니다.</td><th>유의사항</th><td>신청 안내</td></tr>
          </tbody></table>
          <a href="./addGuriUserCourseRegistView.do?key={source.menu_key}&amp;searchEduKey={source.education_key}&amp;searchEduInstSe={source.institution_code}&amp;lctreRcritKey=7001">확인</a>
        </body></html>
        """

    def get(
        self,
        url: str,
        *,
        timeout: int,
        allow_redirects: bool,
    ) -> FakeResponse:
        assert timeout == 7
        assert allow_redirects is False
        self.calls.append(url)
        if url == guri.GURI_RESERVE_URL:
            return FakeResponse(text=self._inventory())
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        source = next(
            source
            for source in guri.GURI_RESERVE_SOURCES
            if source.menu_key == query.get("key", [""])[0]
            and source.institution_code == query.get("searchEduInstSe", [""])[0]
            and source.education_key == query.get("searchEduKey", [""])[0]
        )
        if parsed.path == guri.GURI_RESERVE_LIST_PATH:
            return FakeResponse(
                text=self._list_page(source, int(query.get("pageIndex", ["1"])[0]))
            )
        assert parsed.path == guri.GURI_RESERVE_DETAIL_PATH
        assert query["lctreRcritKey"] == ["7001"]
        return FakeResponse(text=self._detail(source))

    def close(self) -> None:
        self.closed = True


def _reserve_collect(session: FakeReserveSession, **kwargs: Any):
    target = Target(
        provider=guri.GURI_RESERVE_PROVIDER,
        name="구리시 통합예약포털 교육강좌",
        url=guri.GURI_RESERVE_URL,
    )
    return guri.collect_guri_reserve_courses(
        target,
        timeout=7,
        max_pages=kwargs.pop("max_pages", 34),
        detail_limit=kwargs.pop("detail_limit", 10),
        session_factory=lambda: session,
        today=kwargs.pop("today", "2099-06-15"),
        **kwargs,
    )


def test_reserve_collects_all_source_inventories_pages_sentinels_and_detail() -> None:
    session = FakeReserveSession()

    rows, parser, meta = _reserve_collect(session)

    assert parser == guri.GURI_RESERVE_PARSER
    assert len(rows) == 1
    row = rows[0]
    assert row["provider_course_id"] == f"{guri.GURI_RESERVE_PROVIDER}:course:7001"
    assert row["title"] == "주민 디지털교실"
    assert row["branch"] == "갈매동 주민자치센터"
    assert row["status"] == "OPEN"
    assert row["period"] == "2099-07-01 ~ 2099-08-01"
    assert row["apply_period"] == "2099-06-01 ~ 2099-06-30"
    assert row["capacity_total"] == 20
    assert row["reservation_available"] is True
    assert urlparse(row["application_url"]).path == guri.GURI_RESERVE_APPLICATION_PATH
    assert meta["source_count"] == 17
    assert meta["inventory_count"] == 17
    assert meta["source_total"] == 1
    assert meta["source_rows"] == 1
    assert meta["required_list_requests"] == 34
    assert meta["pages"] == 34
    assert meta["current_count"] == 1
    assert meta["detail_pages"] == 1
    assert meta["snapshot_complete"] is True
    assert session.closed is True


def test_reserve_complete_empty_catalogues_are_valid_no_current_data() -> None:
    session = FakeReserveSession(active_row=False)

    rows, _parser, meta = _reserve_collect(session, detail_limit=0)

    assert rows == []
    assert meta["source_total"] == 0
    assert meta["source_rows"] == 0
    assert meta["detail_pages"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True


def test_reserve_inventory_change_and_page_cap_fail_closed() -> None:
    changed = FakeReserveSession(bad_inventory=True)
    rows, _parser, meta = _reserve_collect(changed)
    assert rows == []
    assert "source inventory changed" in meta["configured_collection_error"]

    capped = FakeReserveSession()
    rows, _parser, meta = _reserve_collect(capped, max_pages=33)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "33 of at least 34" in meta["configured_collection_error"]


def test_reserve_nonempty_sentinel_and_detail_mismatch_fail_closed() -> None:
    sentinel = FakeReserveSession(nonempty_sentinel=True)
    rows, _parser, meta = _reserve_collect(sentinel)
    assert rows == []
    assert "sentinel page is not empty" in meta["configured_collection_error"]

    mismatch = FakeReserveSession(detail_title="다른 강좌")
    rows, _parser, meta = _reserve_collect(mismatch)
    assert rows == []
    assert "detail/list title mismatch" in meta["configured_collection_error"]
    assert meta["detail_errors"] == 1


def test_reserve_detail_limit_and_external_dedupe_fail_closed() -> None:
    limited = FakeReserveSession()
    rows, _parser, meta = _reserve_collect(limited, detail_limit=0)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "0 of 1 required current/future details" in meta["configured_collection_error"]

    deduped = FakeReserveSession()
    rows, _parser, meta = _reserve_collect(deduped, dedupe_rows=lambda _rows: [])
    assert rows == []
    assert "dedupe changed complete row count 1 to 0" in meta["configured_collection_error"]


def test_reserve_known_date_correction_requires_exact_source_fingerprint() -> None:
    assert guri._reserve_education_range("2046", "2026-07-01 ~ 2025-09-30") == (
        "2026-07-01",
        "2026-09-30",
        "2026-07-01 ~ 2026-09-30",
        True,
    )
    assert guri._reserve_education_range("2046", "2026-07-02 ~ 2025-09-30") == (
        "",
        "",
        "",
        False,
    )
    assert guri._reserve_education_range("9999", "2026-07-01 ~ 2025-09-30") == (
        "",
        "",
        "",
        False,
    )


def test_reserve_dispatch_target_and_helpers_are_exact() -> None:
    target = Target(
        provider=guri.GURI_RESERVE_PROVIDER,
        url=guri.GURI_RESERVE_URL,
    )
    source = guri.GURI_RESERVE_SOURCES[0]
    assert guri.is_guri_reserve_target(target) is True
    assert guri.is_guri_education_target(target) is True
    assert guri.is_guri_reserve_target(
        Target(provider=guri.GURI_RESERVE_PROVIDER, url=guri.GURI_RESERVE_URL + "&x=1")
    ) is False
    assert guri.guri_reserve_list_url(source, 2).endswith("pageUnit=15&pageIndex=2")
    assert guri.guri_reserve_list_url(source, "../2") == ""
    assert guri.guri_reserve_detail_url(source, "7001").endswith("lctreRcritKey=7001")
    assert guri.guri_reserve_detail_url(source, "7001&evil=1") == ""
