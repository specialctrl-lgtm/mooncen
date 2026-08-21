from __future__ import annotations

from collections import Counter
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from bs4 import BeautifulSoup

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import Crawler_MunicipalYaml as municipal
from utils.generic_course_eligibility import generic_course_row_decision


PROVIDER = municipal.GOYANG_EDUCATION_PROVIDER
TARGET_URL = municipal.GOYANG_EDUCATION_LIST_URL


def _target(*, provider: str = PROVIDER, url: str = TARGET_URL) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=provider,
        name="고양시 통합예약 교육·강좌",
        branch="경기도 고양시",
        url=url,
        source="test",
        priority=1,
        region="경기도 고양시",
        extra={
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
        },
    )


def _card(
    reservation_id: str,
    status: str,
    *,
    title: str,
    place: str,
    category_code: str = municipal.GOYANG_EDUCATION_CATEGORY_CODE,
) -> str:
    schedule_label = (
        "체험"
        if category_code == municipal.GOYANG_EXPERIENCE_CATEGORY_CODE
        else "교육"
    )
    return f"""
    <li>
      <a href="#" onclick="opResveView({reservation_id}, '');">
        <p class="list_type01"><b>{status}</b></p>
        <p class="list_type02"><strong class="subject_tit"><span>{title}</span></strong><span>{place}</span></p>
        <p class="list_type03">전체</p>
        <p class="list_type04"><b>신청 : </b>2099-07-01 ~ 2099-07-31<br/><b>{schedule_label} : </b>2099-08-01 ~ 2099-08-31</p>
        <p class="list_type05"><b>20</b>명</p>
        <p class="list_type06"><span>선착순</span><span>무료</span></p>
        <p class="list_type07"><span>온라인</span></p>
      </a>
    </li>
    """


def _list_page(cards: list[str]) -> str:
    return f"""
    <html><body>
      <form id="dataForm">
        <input name="q_rowPerPage" value="1000"/>
        <input name="q_currPage" value="1"/>
      </form>
      <div class="list-header"><span class="bbs-total">총 <strong>{len(cards)}</strong>건</span></div>
      <ul class="list">{''.join(cards)}</ul>
      <div class="pagination"><strong>1</strong></div>
    </body></html>
    """


def _detail_page(
    reservation_id: str,
    *,
    title: str,
    category_code: str,
    application_button: bool,
) -> str:
    button = (
        "<button type='button' onclick=\"opResveReqst('rcrit', '', '1002');\">온라인예약</button>"
        if application_button
        else ""
    )
    is_experience = category_code == municipal.GOYANG_EXPERIENCE_CATEGORY_CODE
    category_path = "체험·견학 &gt; 안전체험" if is_experience else "교육·강좌 &gt; 정보화교육"
    schedule_header = "체험.견학일시" if is_experience else "교육.강좌 일시"
    venue = "고양시 공식 체험장" if is_experience else "고양시 공식 교육장"
    team = "체험팀" if is_experience else "교육팀"
    return f"""
    <html><body>
      <input name="resveSn" value="{reservation_id}"/>
      <div class="sub-title"><h3>{title}</h3></div>
      <h4 class="h4-title">{category_path}</h4>
      <table><tbody>
        <tr><th>모집정원</th><td>23명 / 25명 ( 모집정원 20명, 대기자 정원 5명 )</td><th>이용대상</th><td>전체</td></tr>
        <tr><th>연령제한</th><td>없음</td><th>{schedule_header}</th><td>2099-08-01 ~ 2099-08-31 매주 월요일 10:00~12:00</td></tr>
        <tr><th>장소</th><td>{venue}</td><th>이용료</th><td>무료</td></tr>
        <tr><th>신청방법</th><td>온라인</td><th>신청기간</th><td>2099-07-01 09:00 ~ 2099-07-31 18:00</td></tr>
        <tr><th>선별방법</th><td>선착순</td><th>담당자</th><td>{team} (031-8075-1111)</td></tr>
        <tr><th>첨부파일</th><td colspan="3"><a href="/attach/program.pdf">첨부파일</a></td></tr>
      </tbody></table>
      {button}
    </body></html>
    """


@pytest.fixture
def goyang_site(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    definitions: dict[str, dict[str, str]] = {}

    def add_rows(
        district: str,
        status_code: str,
        count: int,
        prefix: int,
        *,
        category_code: str = municipal.GOYANG_EDUCATION_CATEGORY_CODE,
    ) -> None:
        for offset in range(1, count + 1):
            reservation_id = str(prefix + offset)
            definitions[reservation_id] = {
                "district": district,
                "status_code": status_code,
                "category_code": category_code,
                "title": (
                    f"공식 체험견학 {reservation_id}"
                    if category_code == municipal.GOYANG_EXPERIENCE_CATEGORY_CODE
                    else f"공식 교육강좌 {reservation_id}"
                ),
                "place": (
                    "고양시 공식 체험장"
                    if category_code == municipal.GOYANG_EXPERIENCE_CATEGORY_CODE
                    else f"공식 교육장 {district}"
                ),
            }

    add_rows("395000000", "1004", 43, 43000)
    add_rows("396010000", "1001", 2, 85100)
    add_rows("396010000", "1002", 1, 85200)
    add_rows("396010000", "1004", 12, 85300)
    add_rows("410010000", "1002", 2, 87100)
    add_rows("410010000", "1004", 5, 87200)
    add_rows("", "1001", 1, 90100, category_code=municipal.GOYANG_EXPERIENCE_CATEGORY_CODE)
    add_rows("", "1002", 6, 90200, category_code=municipal.GOYANG_EXPERIENCE_CATEGORY_CODE)
    add_rows("", "1004", 5, 90400, category_code=municipal.GOYANG_EXPERIENCE_CATEGORY_CODE)

    fetched: list[str] = []
    state: dict[str, Any] = {
        "global_extra": False,
        "detail_failure_id": "",
        "detail_title_mismatch_id": "",
        "detail_category_mismatch_id": "",
    }

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == municipal.GOYANG_EDUCATION_LIST_PATH:
            category_code = query["q_resveTopClCode"][0]
            assert category_code in municipal.GOYANG_OFFICIAL_CATEGORY_LABELS
            assert query["q_rowPerPage"] == ["1000"]
            assert query["q_currPage"] == ["1"]
            status_code = query["q_resveSttusCode"][0]
            assert status_code in municipal.GOYANG_EDUCATION_STATUS_FILTERS
            district = query.get("q_guDeptCode", [""])[0]
            if category_code == municipal.GOYANG_EXPERIENCE_CATEGORY_CODE:
                assert district == ""
                assert "q_dongDeptCode" not in query
            elif district:
                assert query["q_dongDeptCode"] == ["ALL"]
            selected = [
                (reservation_id, definition)
                for reservation_id, definition in definitions.items()
                if definition["category_code"] == category_code
                and definition["status_code"] == status_code
                and (not district or definition["district"] == district)
            ]
            cards = [
                _card(
                    reservation_id,
                    municipal.GOYANG_EDUCATION_STATUS_FILTERS[status_code],
                    title=definition["title"],
                    place=definition["place"],
                    category_code=category_code,
                )
                for reservation_id, definition in selected
            ]
            if (
                state["global_extra"]
                and category_code == municipal.GOYANG_EDUCATION_CATEGORY_CODE
                and not district
                and status_code == "1004"
            ):
                cards.append(
                    _card("99999", "접수마감", title="구 미지정 강좌", place="고양시 교육장")
                )
            return BeautifulSoup(_list_page(cards), "lxml")

        assert parsed.path == municipal.GOYANG_EDUCATION_DETAIL_PATH
        reservation_id = query["resveSn"][0]
        if state["detail_failure_id"] == reservation_id:
            raise RuntimeError("fixture detail outage")
        definition = definitions[reservation_id]
        assert query["q_resveTopClCode"] == [definition["category_code"]]
        title = definition["title"]
        if state["detail_title_mismatch_id"] == reservation_id:
            title = f"다른 상세 제목 {reservation_id}"
        category_code = definition["category_code"]
        if state["detail_category_mismatch_id"] == reservation_id:
            category_code = (
                municipal.GOYANG_EDUCATION_CATEGORY_CODE
                if category_code == municipal.GOYANG_EXPERIENCE_CATEGORY_CODE
                else municipal.GOYANG_EXPERIENCE_CATEGORY_CODE
            )
        return BeautifulSoup(
            _detail_page(
                reservation_id,
                title=title,
                category_code=category_code,
                application_button=definition["status_code"] in {"1001", "1002"},
            ),
            "lxml",
        )

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fetch)
    return {"definitions": definitions, "fetched": fetched, "state": state}


def test_goyang_full_current_snapshot_keeps_education_districts_and_adds_global_experience(
    goyang_site: dict[str, Any],
) -> None:
    rows, parser, meta = municipal.collect_from_url(
        _target(), timeout=7, max_depth=0, max_pages=30, detail_limit=100
    )

    assert parser == municipal.GOYANG_EDUCATION_PARSER
    assert len(rows) == 77
    assert meta["pages"] == 15
    assert meta["detail_pages"] == 77
    assert meta["detail_errors"] == 0
    assert meta["pagination_complete"] is True
    assert meta["list_pagination_complete"] is True
    assert meta["global_union_matches"] is True
    assert meta["district_counts"] == {"395000000": 43, "396010000": 15, "410010000": 7}
    assert meta["status_counts"] == {"1001": 2, "1002": 3, "1004": 60}
    assert meta["global_status_counts"] == meta["status_counts"]
    assert meta["experience_status_counts"] == {"1001": 1, "1002": 6, "1004": 5}
    assert meta["experience_global_status_counts"] == meta["experience_status_counts"]
    assert meta["category_counts"] == {"education": 65, "experience": 12}
    assert meta["domain_category_counts"] == {"교육·강좌": 65, "체험·견학": 12}
    assert meta["service_group_counts"] == {"공공강좌": 65, "체험": 12}
    assert meta["experience_scope_has_district_filters"] is False
    assert meta["reservation_discovery_links"] == 9
    assert "configured_collection_error" not in meta

    expected_districts = {
        "395000000": ("4128100000", "GOYANG_DEOGYANGGU"),
        "396010000": ("4128500000", "GOYANG_ILSAN_DONGGU"),
        "410010000": ("4128700000", "GOYANG_ILSAN_SEOGU"),
    }
    education_rows = [row for row in rows if row["program_type"] == "교육"]
    experience_rows = [row for row in rows if row["program_type"] == "체험"]
    assert len(education_rows) == 65
    assert len(experience_rows) == 12
    for row in education_rows:
        raw_fields = row["raw_fields"]
        reservation_id = raw_fields["reservation_id"]
        assert row["provider_course_id"] == reservation_id
        assert row["raw_url"] == municipal.goyang_education_detail_url(reservation_id)
        assert "q_guDeptCode" not in row["raw_url"]
        assert "q_currPage" not in row["raw_url"]
        municipality_code, branch_code = expected_districts[raw_fields["district_filter"]]
        assert row["municipality_code"] == municipality_code
        assert row["branch_code"] == branch_code
        assert row["preserve_branch"] is True
        assert row["collection_category"] == "공공예약"
        assert row["domain_category"] == "교육·강좌"
        assert row["source_group"] == "municipal_reservation"
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["category"] == "정보화교육"
        assert row["venue_name"] == "고양시 공식 교육장"
        assert row["capacity_current"] == 20
        assert row["capacity_total"] == 20
        assert row["waitlist_current"] == 3
        assert row["waitlist_total"] == 5
        assert row["phone"] == "031-8075-1111"

    for row in experience_rows:
        raw_fields = row["raw_fields"]
        reservation_id = raw_fields["reservation_id"]
        assert row["provider_course_id"] == reservation_id
        assert row["raw_url"] == municipal.goyang_education_detail_url(
            reservation_id,
            category_code=municipal.GOYANG_EXPERIENCE_CATEGORY_CODE,
        )
        assert parse_qs(urlparse(row["raw_url"]).query)["q_resveTopClCode"] == ["CL_02"]
        assert raw_fields["official_top_category_code"] == "CL_02"
        assert raw_fields["official_top_category"] == "체험·견학"
        assert raw_fields["district_filter"] == ""
        assert raw_fields["detail_title"] == row["title"]
        assert row["municipality_code"] == "4128000000"
        assert row["municipality_full_name"] == "경기도 고양시"
        assert row["branch_code"] == "GOYANG_EXPERIENCE"
        assert row["preserve_branch"] is True
        assert row["collection_category"] == "공공예약"
        assert row["domain_category"] == "체험·견학"
        assert row["service_group"] == "체험"
        assert row["service_group_policy"] == "locked"
        assert row["category"] == "안전체험"
        assert row["venue_name"] == "고양시 공식 체험장"

    by_filter = Counter(row["raw_fields"]["status_filter"] for row in education_rows)
    assert by_filter == Counter({"1004": 60, "1002": 3, "1001": 2})
    assert all(row.get("application_url") == row["raw_url"] for row in rows if row["raw_fields"]["status_filter"] == "1002")
    assert all(row["reservation_available"] is True for row in rows if row["raw_fields"]["status_filter"] == "1002")
    for row in rows:
        if row["raw_fields"]["status_filter"] != "1002":
            assert "application_url" not in row
            assert row["reservation_available"] is False
            assert row["raw_fields"]["clear_application_url"] is True

    list_queries = [
        parse_qs(urlparse(url).query, keep_blank_values=True)
        for url in goyang_site["fetched"]
        if urlparse(url).path == municipal.GOYANG_EDUCATION_LIST_PATH
    ]
    assert len(list_queries) == 15
    assert all(query["q_resveSttusCode"][0] != "1005" for query in list_queries)
    experience_queries = [query for query in list_queries if query["q_resveTopClCode"] == ["CL_02"]]
    assert len(experience_queries) == 3
    assert all("q_guDeptCode" not in query for query in experience_queries)
    assert all("q_dongDeptCode" not in query for query in experience_queries)


def test_goyang_global_partition_mismatch_is_incomplete_and_skips_details(
    goyang_site: dict[str, Any],
) -> None:
    goyang_site["state"]["global_extra"] = True
    rows, _parser, meta = municipal.collect_goyang_education_reservations(
        _target(), timeout=5, max_pages=30, detail_limit=100
    )

    assert len(rows) == 77
    assert meta["pages"] == 15
    assert meta["detail_pages"] == 0
    assert meta["global_union_matches"] is False
    assert meta["pagination_complete"] is False
    assert "district union did not match global status=1004 set" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("state_key", "reservation_id"),
    [
        ("detail_title_mismatch_id", "90201"),
        ("detail_category_mismatch_id", "90201"),
    ],
)
def test_goyang_experience_detail_must_match_list_identity_title_and_official_category(
    goyang_site: dict[str, Any],
    state_key: str,
    reservation_id: str,
) -> None:
    goyang_site["state"][state_key] = reservation_id

    rows, _parser, meta = municipal.collect_goyang_education_reservations(
        _target(), timeout=5, max_pages=30, detail_limit=100
    )

    assert len(rows) == 77
    assert meta["pages"] == 15
    assert meta["detail_pages"] == 77
    assert meta["detail_errors"] == 1
    assert meta["pagination_complete"] is False
    assert f"detail parse failed for reservation {reservation_id}" in meta["configured_collection_error"]


@pytest.mark.parametrize("failure", ["cap", "detail"])
def test_goyang_incomplete_detail_blocks_persistence_and_stale(
    goyang_site: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    if failure == "detail":
        goyang_site["state"]["detail_failure_id"] = "43001"
    stale_calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        generated,
        "get_db_connection",
        lambda: (_ for _ in ()).throw(AssertionError("incomplete Goyang crawl must not open a DB transaction")),
    )
    monkeypatch.setattr(generated, "mark_stale_courses", lambda *args: stale_calls.append(args) or 0)

    result = generated._collect_single_target(
        _target(),
        per_target_limit=0,
        max_depth=0,
        max_pages=30,
        detail_limit=64 if failure == "cap" else 100,
        timeout=5,
    )

    assert result.collection_complete is False
    if failure == "cap":
        assert result.report.detail_pages == 64
        assert "detail enrichment capped at 64 of 77" in result.report.configured_collection_error
    else:
        assert result.report.detail_pages == 77
        assert "detail fetch failed for reservation 43001" in result.report.configured_collection_error

    generated._persist_collection_results(
        [result],
        mark_stale=True,
        max_pages=30,
        per_target_limit=0,
        complete_providers={PROVIDER},
    )
    assert result.report.saved == 0
    assert result.report.success is False
    assert stale_calls == []


@pytest.mark.parametrize(
    "target",
    [
        _target(provider="MUNI_WWW_GOYANG_GO_KR_9C1A7354"),
        _target(url="http://www.goyang.go.kr/resve/manage/BD_selectResveManageList.do?q_resveTopClCode=CL_01"),
        _target(url="https://goyang.go.kr/resve/manage/BD_selectResveManageList.do?q_resveTopClCode=CL_01"),
        _target(url="https://www.goyang.go.kr/resve/manage/BD_selectResveManageList.do?q_resveTopClCode=CL_02"),
        _target(url="https://www.goyang.go.kr/resve/manage/BD_selectResveManageList.do?q_resveTopClCode=CL_01#fragment"),
    ],
)
def test_goyang_route_and_owner_are_exact(target: municipal.CrawlTarget) -> None:
    assert municipal.is_goyang_education_target(target) is False


def test_goyang_experience_scope_is_global_and_requires_official_experience_list_shape() -> None:
    assert (
        municipal.goyang_education_list_url(
            "395000000",
            "1002",
            1,
            category_code=municipal.GOYANG_EXPERIENCE_CATEGORY_CODE,
        )
        == ""
    )
    experience_url = municipal.goyang_education_list_url(
        "",
        "1002",
        1,
        category_code=municipal.GOYANG_EXPERIENCE_CATEGORY_CODE,
    )
    query = parse_qs(urlparse(experience_url).query, keep_blank_values=True)
    assert query["q_resveTopClCode"] == ["CL_02"]
    assert "q_guDeptCode" not in query
    assert "q_dongDeptCode" not in query

    wrong_list_shape = BeautifulSoup(
        _card(
            "7133",
            "접수중",
            title="공식 체험 예약",
            place="고양시 시민안전체험관",
            category_code=municipal.GOYANG_EDUCATION_CATEGORY_CODE,
        ),
        "lxml",
    )
    assert (
        municipal.goyang_education_card_row(
            _target(),
            wrong_list_shape.select_one("a"),
            "",
            "1002",
            category_code=municipal.GOYANG_EXPERIENCE_CATEGORY_CODE,
        )
        is None
    )


def test_goyang_service_wait_placeholder_is_rejected_by_common_and_owned_paths() -> None:
    placeholder = "서비스 접속 대기 중입니다."
    eligible, reason = generic_course_row_decision(
        {
            "title": placeholder,
            "period": "2099-08-01 ~ 2099-08-31",
            "apply_period": "2099-07-01 ~ 2099-07-31",
            "target": "전체",
            "venue_name": "고양시 교육장",
            "raw_url": municipal.goyang_education_detail_url("6848"),
        }
    )
    assert eligible is False
    assert reason == "service_access_placeholder"

    soup = BeautifulSoup(
        _card("6848", "접수중", title=placeholder, place="덕양구청"),
        "lxml",
    )
    assert municipal.goyang_education_card_row(
        _target(),
        soup.select_one("a"),
        "395000000",
        "1002",
    ) is None


def test_goyang_target_duplicate_exclusions_coverage_and_full_run_contract() -> None:
    document = yaml.safe_load(
        (municipal.ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml").read_text(encoding="utf-8")
    )
    canonical = next(row for row in document["targets"] if row["provider"] == PROVIDER)
    assert canonical["url"] == TARGET_URL
    assert canonical["service_group"] == "공공강좌"
    assert canonical["service_group_policy"] == "locked"
    assert canonical["ops_scopes"] == ["education", "experience"]
    assert [row["code"] for row in canonical["covered_municipalities"]] == [
        "4128000000",
        "4128100000",
        "4128500000",
        "4128700000",
    ]

    duplicates = [
        row
        for row in document["targets"]
        if row["provider"] in {"MUNI_WWW_GOYANG_GO_KR_9C1A7354", "MUNI_WWW_GOYANG_GO_KR_C66631A8"}
    ]
    assert len(duplicates) == 4
    assert all(row["collection_type"] == "duplicate" for row in duplicates)
    assert all(row["crawler_status"] == f"duplicate_url:{PROVIDER}" for row in duplicates)
    assert all(row["duplicate_of"] == PROVIDER for row in duplicates)

    arguments = list(generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[PROVIDER])
    assert arguments == [
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "30",
        "--detail-limit",
        "100",
    ]
    parsed = generated.parse_args(["--provider", PROVIDER, *arguments])
    assert parsed.mark_stale is True
    assert parsed.per_target_limit == 0
    assert parsed.allow_partial_save is False
