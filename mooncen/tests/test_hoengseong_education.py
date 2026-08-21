from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import os
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_hoengseong as hoengseong


@dataclass
class Target:
    provider: str
    url: str
    candidate_id: str = ""


class Response:
    def __init__(
        self,
        body: str | bytes,
        url: str = "",
        *,
        status: int = 200,
        content_type: str = "text/html; charset=UTF-8",
    ) -> None:
        self.content = body.encode("utf-8") if isinstance(body, str) else body
        self.status_code = status
        self.url = url
        self.history: list[object] = []
        self.headers = {"Content-Type": content_type}


class Session:
    def close(self) -> None:
        pass


def _target(owner: str) -> Target:
    config = hoengseong.HOENGSEONG_OWNERS[owner]
    return Target(config["provider"], config["url"], config["candidate_id"])


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "html.parser")


def _culture_html(*, title: str = "서예(한글)") -> str:
    return f"""
    <html><head><title>횡성문화원</title></head><body>
      <div class="culWrap">
        <h2>학교명</h2><p class="culDesc">횡성문화원 문화학교 제27기</p>
        <h2>모집 개요</h2><p class="culDesc">
          ＊ 모집기간 : 2026년 2월 23일 ~<br>
          ＊ 모집대상 : 횡성관내주민<br>
          ＊ 모집강좌 : 1개 강좌<br>
          ＊ 특별회원회비 : 연 40,000원<br>
        </p>
        <h2>운영 기간</h2><p class="culDesc">
          ＊ 운영기간 : 2026년 3월 9일(월) ~ 12월 4일(금)
        </p>
        <h2>강좌별 운영현황</h2>
        <div class="cul_list"><table>
          <tr><th>순서</th><th>프 로 그 램 명</th><th>강 의 요 일 / 시 간</th>
              <th>모집 인원</th><th>교 육 내 용</th><th>비고</th></tr>
          <tr><td>1</td><td>{title}</td><td>월 · 수 / 10:00 ~ 12:00</td>
              <td>20명</td><td>서예 기초</td><td>문화교실 1</td></tr>
        </table></div>
      </div>
    </body></html>
    """


def test_owner_registry_has_six_disjoint_canonical_sources() -> None:
    assert set(hoengseong.HOENGSEONG_OWNERS) == {
        "reservation",
        "municipal_library",
        "education_library",
        "youth_center",
        "culture_school",
        "family_center",
    }
    providers = [config["provider"] for config in hoengseong.HOENGSEONG_OWNERS.values()]
    urls = [config["url"] for config in hoengseong.HOENGSEONG_OWNERS.values()]
    candidates = [
        config["candidate_id"] for config in hoengseong.HOENGSEONG_OWNERS.values()
    ]
    assert len(providers) == len(set(providers))
    assert len(urls) == len(set(urls))
    assert len(candidates) == len(set(candidates))


def test_candidate_ids_follow_canonical_url_sha256() -> None:
    for config in hoengseong.HOENGSEONG_OWNERS.values():
        digest = hashlib.sha256(config["url"].encode()).hexdigest()[:12].upper()
        assert config["candidate_id"] == f"MUNI_IR_{digest}"


def test_new_provider_ids_follow_host_and_url_hashes_but_incumbent_is_retained() -> None:
    assert (
        hoengseong.HOENGSEONG_RESERVATION_PROVIDER
        == "MUNI_WWW_HSG_GO_KR_7452F27B"
    )
    for owner, config in hoengseong.HOENGSEONG_OWNERS.items():
        if owner == "reservation":
            continue
        host = re.sub(r"[^A-Z0-9]+", "_", urlparse(config["url"]).hostname.upper()).strip(
            "_"
        )
        digest = hashlib.sha1(config["url"].encode()).hexdigest()[:8].upper()
        assert config["provider"] == f"MUNI_{host}_{digest}"


def test_candidate_audit_retargets_home_and_excludes_attachment() -> None:
    audit = hoengseong.HOENGSEONG_CANDIDATE_AUDIT
    assert audit["MUNI_IR_90310635DC35"]["decision"].startswith("retarget_")
    assert audit["MUNI_IR_A72756E021AA"]["decision"] == (
        "exclude_attachment_not_course_ledger"
    )


def test_official_reservation_branch_registry_is_exact() -> None:
    assert [item.branch for item in hoengseong.HOENGSEONG_RESERVATION_CATEGORIES] == [
        "횡성군평생학습관",
        "횡성군여성회관",
        "횡성군립도서관",
        "둔내태성도서관",
        "횡성군청소년수련관",
        "횡성읍 주민자치센터",
        "우천면 주민자치센터",
        "안흥면 주민자치센터",
        "둔내면 주민자치센터",
        "갑천면 주민자치센터",
        "청일면 주민자치센터",
        "공근면 주민자치센터",
        "서원면 주민자치센터",
        "강림면 주민자치센터",
    ]
    assert len({item.category_id for item in hoengseong.HOENGSEONG_RESERVATION_CATEGORIES}) == 14


def test_target_selection_is_exact_provider_and_url_pair() -> None:
    for owner in hoengseong.HOENGSEONG_OWNERS:
        target = _target(owner)
        assert hoengseong.owner_for_target(target) == owner
        assert hoengseong.is_target(target)
        assert not hoengseong.is_target(Target(target.provider, target.url + "#bad"))
        assert not hoengseong.is_target(Target("MUNI_WRONG", target.url))


def test_url_allowlist_rejects_application_login_result_attachment_and_support_routes() -> None:
    forbidden = [
        (
            "reservation",
            "GET",
            "https://www.hsg.go.kr/reserve/downloadEdcCoursePlaceAtchFile.do?fileNo=1",
        ),
        (
            "municipal_library",
            "GET",
            hoengseong.HOENGSEONG_LIBRARY_URL
            + "&act=lecture_result_view&lgCode=9&leCode=446&cate=",
        ),
        (
            "education_library",
            "GET",
            "https://lib.gwe.go.kr/hslib/menu/4287/user/my/lecture-event/list",
        ),
        (
            "youth_center",
            "GET",
            "https://hsyouthcenter.hsg.go.kr/bbs/board.php?bo_table=backup",
        ),
        (
            "family_center",
            "POST",
            "https://hsg.familynet.or.kr/recruitReceipt/loginCheck.do",
        ),
        (
            "family_center",
            "POST",
            "https://hsg.familynet.or.kr/recruitReceipt/modal/apply.do",
        ),
    ]
    for owner, method, url in forbidden:
        with pytest.raises(hoengseong.HoengseongContractError):
            hoengseong._classify_url(owner, method, url)


def test_public_url_builders_are_allowlisted() -> None:
    assert hoengseong._classify_url(
        "reservation", "GET", hoengseong.reservation_list_url(9)
    ) == "list"
    assert hoengseong._classify_url(
        "reservation", "GET", hoengseong.reservation_detail_url("706", 1)
    ) == "detail"
    assert hoengseong._classify_url(
        "municipal_library", "GET", hoengseong.library_detail_url("446")
    ) == "detail"
    assert hoengseong._classify_url(
        "education_library", "GET", hoengseong.gwe_detail_url("9191")
    ) == "detail"
    assert hoengseong._classify_url(
        "youth_center", "GET", hoengseong.youth_detail_url("40")
    ) == "detail"
    assert hoengseong._classify_url(
        "family_center", "POST", hoengseong.HOENGSEONG_FAMILY_VIEW_API_URL
    ) == "detail_api"


def test_date_parser_handles_full_partial_two_digit_and_month_ranges() -> None:
    assert hoengseong._date_pair("2026. 8. 11. (화)", "x") == (
        date(2026, 8, 11),
        date(2026, 8, 11),
    )
    assert hoengseong._date_pair("26 . 2. 24.", "x") == (
        date(2026, 2, 24),
        date(2026, 2, 24),
    )
    assert hoengseong._date_pair(
        "7월 ~ 11월 첫째·셋째 수요일", "x", default_year=2026
    ) == (date(2026, 7, 1), date(2026, 11, 30))


def test_classification_is_locked_and_experience_rule_is_explicit() -> None:
    craft = hoengseong._base_row("youth_center", "1", "팔찌 만들기", "횡성군 청년센터")
    lecture = hoengseong._base_row("youth_center", "2", "영어 회화", "횡성군 청년센터")
    assert (craft["domain_category"], craft["service_group"]) == (
        "체험·견학",
        "체험",
    )
    assert (lecture["domain_category"], lecture["service_group"]) == (
        "교육·강좌",
        "공공강좌",
    )
    for row in (craft, lecture):
        assert row["collection_category"] == "공공예약"
        assert row["source_group"] == "municipal_reservation"
        assert row["collection_type"] == "locked"
        assert row["classification_locked"] is True


def test_hsg_list_parser_reads_exact_table_contract() -> None:
    html = """
    <html><head><title>평생학습 - 통합예약</title></head><body>
      <table><thead><tr>
        <th>No.</th><th>강좌명</th><th>접수기간 /교육기간</th><th>교육요일/시간</th>
        <th>선발방법</th><th>신청/모집(대기자)</th><th>신청방법</th><th>접수상태</th>
      </tr></thead><tbody><tr>
        <td>1</td><td><a href="./selectEdcCourseLctreRcritViewU.do?key=1670&amp;searchLctreRcritKey=706&amp;pageUnit=10&amp;searchCnd=all&amp;pageIndex=1">강좌</a></td>
        <td><span class="js">2026-07-22 ~ 2026-08-07</span><span class="ky">2026-11-07 ~ 2026-11-28</span></td>
        <td>토/10:00 ~ 12:00</td><td>선착순</td><td>2 / 13 (0 / 10)</td>
        <td>인터넷</td><td>접수중</td>
      </tr></tbody></table>
    </body></html>
    """
    page = hoengseong._hsg_list_page(_soup(html), 1, "평생학습", "횡성군평생학습관")
    assert page.rows[0]["source_identity"] == "706"
    assert page.rows[0]["branch"] == "횡성군평생학습관"
    assert page.rows[0]["end"] == date(2026, 11, 28)


def test_library_list_parser_never_follows_registration_result_link() -> None:
    html = """
    <html><head><title>횡성군립도서관 - 프로그램 신청</title></head><body>
      <table class="tstyle responsive"><thead><tr><th>강좌명</th><th>모집인원/대상</th><th>접수일/수강일</th><th>접수현황</th></tr></thead>
      <tbody><tr><td><a href="./index.php?g_page=culture&amp;m_page=culture01&amp;act=lecture_view&amp;lgCode=9&amp;leCode=446&amp;cate=">키링 만들기</a></td>
      <td>15명 모집 0명 신청</td><td><div class="red fb">2026.07.24 ~ 2026.07.31</div><div class="blue fb">2026.08.12</div></td>
      <td><span class="type green">대기중</span><a href="./index.php?act=lecture_result_view">등록확인</a></td></tr></tbody></table>
      <div class="paging"><strong>1</strong></div>
    </body></html>
    """
    rows = hoengseong._library_list_page(_soup(html))
    assert [row["source_identity"] for row in rows] == ["446"]
    assert rows[0]["capacity_total"] == 15


def test_gwe_empty_sentinel_is_exact() -> None:
    html = """
    <html><head><title>프로그램신청</title></head><body>
      <ul class="lecture_result_list"><li class="no_data">조회되는 문화강좌가 없습니다.</li></ul>
    </body></html>
    """
    assert hoengseong._gwe_page(_soup(html), 2).empty
    bad = html.replace("조회되는 문화강좌가 없습니다.", "")
    with pytest.raises(hoengseong.HoengseongContractError):
        hoengseong._gwe_page(_soup(bad), 2)


def test_youth_page_preserves_official_center_name_and_partial_month_period() -> None:
    html = """
    <html><head><title>횡성군 청년센터</title></head><body>
      <form id="fboardlist"><input name="bo_table" value="center"><input name="page" value="1">
      <ul class="board_gallery_list"><li class="end">
        <a href="https://hsyouthcenter.hsg.go.kr/bbs/board.php?bo_table=center&amp;wr_id=38">
          <div class="text"><p class="tit">영어 회화</p>
            <div class="date_list"><div class="icon"><p>모집기간</p></div><p>2026-06-08 ~ 2026-06-22</p></div>
            <div class="date_list"><div class="icon"><p>교육일정</p></div><p>7월 ~ 11월 첫째주 수요일</p></div>
          </div><div class="badge">모집중</div><div class="badge">모집마감</div>
        </a><div class="member_list"><div class="count"><p>모집인원</p><p>10명 / 10명</p></div></div>
      </li></ul></form>
    </body></html>
    """
    row = hoengseong._youth_page(_soup(html), 1).rows[0]
    assert row["branch"] == "횡성군 청년센터"
    assert (row["start"], row["end"]) == (date(2026, 7, 1), date(2026, 11, 30))
    assert row["source_status"] == "모집마감"


def test_family_page_requires_exact_owner_codes_and_empty_marker() -> None:
    html = """
    <html><head><title>횡성군 가족센터&gt;프로그램안내&gt;프로그램신청</title></head><body>
      <form id="searchForm" action="/center/lay1/program/S295T322C451/recruitReceipt/list.do" method="get">
        <input name="rows" value="5"><input name="cpage" value="3">
        <input name="area" value="A004"><input name="area_detail" value="D083">
      </form><div class="program_list"><ul>프로그램 목록이 존재하지 않습니다.</ul></div>
    </body></html>
    """
    assert hoengseong._family_page(_soup(html), 3).empty
    with pytest.raises(hoengseong.HoengseongContractError):
        hoengseong._family_page(_soup(html.replace("D083", "D999")), 3)


def test_culture_collector_is_complete_stable_and_pii_minimized() -> None:
    calls: list[str] = []

    def fetcher(session, method, url, **kwargs):
        calls.append(url)
        return Response(_culture_html(), url)

    rows, parser, meta = hoengseong.collect(
        _target("culture_school"),
        today="2026-07-23",
        fetcher=fetcher,
        session_factory=Session,
    )
    assert parser == hoengseong.HOENGSEONG_PARSER
    assert len(rows) == 1
    assert calls == [hoengseong.HOENGSEONG_CULTURE_URL] * 2
    assert meta["snapshot_complete"] is True
    assert meta["source_rows"] == meta["current_source_count"] == 1
    assert rows[0]["branch"] == "횡성문화원 문화학교"
    assert not hoengseong._privacy_errors(rows[0])
    assert rows[0]["application_url"] == ""


def test_raw_requests_require_explicit_opt_in_or_managed_transport() -> None:
    rows, _, meta = hoengseong.collect(_target("culture_school"), today="2026-07-23")
    assert rows == []
    assert "raw requests disabled" in meta["configured_collection_error"]


def test_access_restriction_fails_closed() -> None:
    def fetcher(session, method, url, **kwargs):
        return Response("<html><head><title>Access Denied</title></head><body>captcha</body></html>", url)

    rows, _, meta = hoengseong.collect(
        _target("culture_school"),
        today="2026-07-23",
        fetcher=fetcher,
        session_factory=Session,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "access restriction" in meta["configured_collection_error"]


def test_dedupe_cardinality_change_fails_closed() -> None:
    rows, _, meta = hoengseong.collect(
        _target("culture_school"),
        today="2026-07-23",
        fetcher=lambda session, method, url, **kwargs: Response(_culture_html(), url),
        session_factory=Session,
        dedupe_rows=lambda rows: [],
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]


def test_invalid_limits_and_noncanonical_target_fail_closed_without_fetch() -> None:
    calls = 0

    def fetcher(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("must not fetch")

    rows, _, meta = hoengseong.collect(
        Target("bad", hoengseong.HOENGSEONG_CULTURE_URL),
        fetcher=fetcher,
        session_factory=Session,
    )
    assert rows == [] and "non-canonical" in meta["configured_collection_error"]
    rows, _, meta = hoengseong.collect(
        _target("culture_school"),
        max_pages=0,
        fetcher=fetcher,
        session_factory=Session,
    )
    assert rows == [] and meta["configured_collection_error"] == "invalid collection limits"
    assert calls == 0


@pytest.mark.skipif(
    os.getenv("RUN_HOENGSEONG_LIVE") != "1",
    reason="set RUN_HOENGSEONG_LIVE=1 for two complete official-source snapshots",
)
def test_live_all_six_owners_are_stable_complete_and_route_safe() -> None:
    for owner, config in hoengseong.HOENGSEONG_OWNERS.items():
        snapshots = []
        for _ in range(2):
            rows, _, meta = hoengseong.collect(
                Target(config["provider"], config["url"], config["candidate_id"]),
                today="2026-07-23",
                max_pages=20,
                detail_limit=100,
                max_workers=6,
                allow_raw_requests_for_tests=True,
            )
            assert meta["configured_collection_error"] == "", (owner, meta)
            assert meta["snapshot_complete"] is True
            assert meta["pagination_complete"] is True
            assert meta["details_complete"] is True
            assert meta["application_endpoint_requests"] == 0
            assert meta["applicant_endpoint_requests"] == 0
            assert meta["login_endpoint_requests"] == 0
            assert meta["attachment_endpoint_requests"] == 0
            assert meta["pii_values_persisted"] == 0
            assert all(not hoengseong._privacy_errors(row) for row in rows)
            snapshots.append(
                (
                    meta["source_identity_sha256"],
                    meta["output_identity_sha256"],
                    tuple(row["provider_course_id"] for row in rows),
                )
            )
        assert snapshots[0] == snapshots[1]
