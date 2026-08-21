from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from bs4 import BeautifulSoup

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import Crawler_MunicipalIntegratedReservation as aggregate
from Crawler import Crawler_MunicipalYaml as municipal


PROVIDER = municipal.JONGNO_RESERV_PROVIDER
TARGET_URL = municipal.JONGNO_RESERV_LIST_URL


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _target(*, provider: str = PROVIDER, url: str = TARGET_URL) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=provider,
        name="종로구 통합예약",
        branch="서울특별시 종로구",
        url=url,
        source="test",
        priority=1,
        region="서울특별시 종로구",
        extra={
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
        },
    )


def _card(pg_id: str, status: str, title: str, venue: str = "종로 교육장") -> str:
    return f"""
    <li>
      <a href="/reserv/detail.do?pgId={pg_id}">
        <span class="cp">{status}</span><span class="tit">{title}</span>
      </a>
      <div class="bot-area">
        <dl><dt>기간</dt><dd>2099-08-01 ~ 2099-08-31</dd></dl>
        <dl><dt>장소</dt><dd>{venue}</dd></dl>
        <dl><dt>모집</dt><dd>20명</dd></dl>
      </div>
    </li>
    """


def _list_page(cards: str, *, total_count: int, current: int) -> str:
    total_pages = max(1, (total_count + municipal.JONGNO_RESERV_PAGE_SIZE - 1) // municipal.JONGNO_RESERV_PAGE_SIZE)
    return f"""
    <html><body><div id="container">
      <p>총 {total_count:,}건</p>
      <ul class="mgs-line">{cards}</ul>
      <div class="page">
        <a href="javascript:linkPage({current});" class="on">{current}</a>
        <a href="javascript:linkPage({total_pages});" title="마지막 페이지">마지막</a>
      </div>
    </div></body></html>
    """


def _application_url(site_id: str, pg_id: str) -> str:
    if site_id == "1":
        return (
            "https://jachi.jongno.go.kr/Program/Default.aspx?"
            f"pageNo=1&areaSeq=47&programKindSeq=124&recommendTargetSeq=130&titleKeyword={pg_id}"
        )
    if site_id == "3":
        return f"https://www.jfac.or.kr/site/main/program/educ_always_list_view?pgIdx={pg_id.removeprefix('JFAC')}"
    return f"https://www.jongno.go.kr/edu/eduApplyview.do?edu_open_cd={pg_id.removeprefix('EDU_')}&pageIndex=1"


def _detail_page(
    pg_id: str,
    site_id: str,
    category: str,
    status: str,
    title: str,
    venue: str,
    *,
    application_url: str | None = None,
    start: str = "2099-08-01",
    end: str = "2099-08-31",
) -> str:
    application_url = application_url if application_url is not None else _application_url(site_id, pg_id)
    escaped_application_url = application_url.replace("&", "&amp;")
    return f"""
    <html><body><div id="container">
      <div class="tit-area"><h3>{title}</h3></div>
      <dl><dt>진행상태</dt><dd>{status}</dd></dl>
      <dl><dt>교육기간</dt><dd>{start} ~ {end}</dd></dl>
      <dl><dt>신청기간</dt><dd>2099-07-01 ~ 2099-07-31</dd></dl>
      <dl><dt>요일</dt><dd>월요일</dd></dl>
      <dl><dt>교육시간</dt><dd>10:00 ~ 12:00</dd></dl>
      <dl><dt>수강료</dt><dd>무료</dd></dl>
      <dl><dt>신청대상</dt><dd>종로구민</dd></dl>
      <dl><dt>접수방법</dt><dd>온라인</dd></dl>
      <dl><dt>모집인원</dt><dd>20명</dd></dl>
      <dl><dt>장소</dt><dd>{venue}</dd></dl>
      <script>let siteId = '{site_id}'; const category = '{category}';</script>
      <a class="btn-tp1 rn" href="javascript:linkPage('{escaped_application_url}');">신청</a>
    </div></body></html>
    """


def _fake_fetcher(
    pages: dict[int, str],
    details: Callable[[str], str],
    fetched: list[str] | None = None,
) -> Callable[[object, str, int], BeautifulSoup]:
    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        if fetched is not None:
            fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == municipal.JONGNO_RESERV_LIST_PATH:
            return _soup(pages[int((query.get("pageIndex") or ["1"])[0])])
        assert parsed.path == "/reserv/detail.do"
        return _soup(details(query["pgId"][0]))

    return fetch


def _full_fixture() -> tuple[dict[int, str], dict[str, str]]:
    records: list[tuple[str, str, str, str, str, str]] = []
    for index in range(1, 196):
        pg_id = f"ACMS_{index}"
        title = "[종로1~4가동] 한국무용" if index == 28 else f"[청운효자동] 자치회관 강좌 {index}"
        venue = "" if index == 28 else f"자치회관 교육장 {index}"
        category = "9" if index == 195 else str((index - 1) % 5 + 1)
        records.append((pg_id, "1", category, "접수중", title, venue))

    jfac_ids = ["JFAC2644", *[f"JFAC{1000 + index}" for index in range(2, 24)]]
    for index, pg_id in enumerate(jfac_ids):
        category = "1" if index < 19 else "2"
        if category == "1":
            status = "접수중" if index < 11 else "접수준비"
        else:
            status = "접수중" if index < 21 else "접수준비"
        title = "청운문학도서관 어린이 글쓰기" if pg_id == "JFAC2644" else f"종로문화재단 프로그램 {pg_id}"
        venue = "" if pg_id == "JFAC2644" else f"문화재단 교육장 {index}"
        records.append((pg_id, "3", category, status, title, venue))

    for index in range(1, 43):
        pg_id = f"EDU_{index}"
        records.append((pg_id, "5", str((index - 1) % 5), "접수준비", f"정보화교육 {index}", f"정보화교육장 {index}"))

    details = {
        pg_id: _detail_page(pg_id, site_id, category, status, title, venue)
        for pg_id, site_id, category, status, title, venue in records
    }
    cards = [_card(pg_id, status, title, venue) for pg_id, _site, _category, status, title, venue in records]
    pages = {
        page: _list_page("".join(cards[(page - 1) * 6 : page * 6]), total_count=260, current=page)
        for page in range(1, 45)
    }
    return pages, details


def test_jongno_full_260_contract_returns_strict_256_education_rows(monkeypatch) -> None:
    pages, details = _full_fixture()
    fetched: list[str] = []
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", _fake_fetcher(pages, details.__getitem__, fetched))

    rows, parser, meta = municipal.collect_from_url(
        _target(), timeout=7, max_depth=0, max_pages=50, detail_limit=500
    )

    assert parser == municipal.JONGNO_RESERV_PARSER
    assert len(rows) == 256
    assert meta["pages"] == 44
    assert meta["total_pages"] == 44
    assert meta["total_count"] == 260
    assert meta["discovered_links"] == 260
    assert meta["detail_attempts"] == meta["detail_pages"] == 260
    assert meta["excluded_non_education_count"] == 4
    assert meta["status_counts"] == {"OPEN": 206, "SCHEDULED": 50}
    assert meta["pagination_complete"] is True
    assert meta["list_pagination_complete"] is True
    assert meta["source_cap_reached"] is False
    assert "configured_collection_error" not in meta
    assert len({row["provider_course_id"] for row in rows}) == 256
    assert not any(row["provider_course_id"] in {"JFAC1020", "JFAC1021", "JFAC1022", "JFAC1023"} for row in rows)
    assert len([url for url in fetched if urlparse(url).path == municipal.JONGNO_RESERV_LIST_PATH]) == 44
    assert len([url for url in fetched if urlparse(url).path == "/reserv/detail.do"]) == 260

    by_id = {row["provider_course_id"]: row for row in rows}
    assert by_id["ACMS_28"]["branch"] == "종로1~4가동 자치회관"
    assert by_id["ACMS_28"]["venue_name"] == "종로1~4가동 자치회관"
    assert by_id["JFAC2644"]["branch"] == "청운문학도서관"
    assert by_id["JFAC2644"]["venue_name"] == "청운문학도서관"
    assert by_id["ACMS_195"]["raw_fields"]["source_category"] == "9"
    assert by_id["ACMS_195"]["category"] == "종로구 통합예약 교육"
    for row in rows:
        assert row["application_url"].startswith("https://")
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["collection_category"] == "공공예약"
        assert row["domain_category"] == "교육·강좌"
        assert row["raw_fields"]["application_url_source"] == "detail_javascript_linkPage"


def test_jongno_recovers_nine_edu_details_with_omitted_script_identity(monkeypatch) -> None:
    pg_ids = [f"EDU_{9000 + index}" for index in range(1, 10)]
    cards = [_card(pg_id, "접수중", f"정보화교육 {index}") for index, pg_id in enumerate(pg_ids, 1)]
    pages = {
        page: _list_page("".join(cards[(page - 1) * 6 : page * 6]), total_count=9, current=page)
        for page in range(1, 3)
    }
    details = {
        pg_id: _detail_page(pg_id, "", "", "접수중", f"정보화교육 {index}", "정보화교육장")
        for index, pg_id in enumerate(pg_ids, 1)
    }
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", _fake_fetcher(pages, details.__getitem__))

    rows, _parser, meta = municipal.collect_jongno_reserv_deadline(
        _target(), timeout=5, max_pages=2, detail_limit=20
    )

    assert len(rows) == 9
    assert meta["source_identity_errors"] == 0
    assert meta["source_identity_recovered_count"] == 9
    assert meta["source_counts"] == {"5:": 9}
    assert meta["application_errors"] == 0
    assert meta["pagination_complete"] is True
    assert "configured_collection_error" not in meta
    assert municipal.jongno_reserv_is_education_source("5", "") is True
    assert municipal.jongno_reserv_is_education_source("5", "9") is False
    assert all(
        row["raw_fields"]["source_identity_provenance"] == "stable_pg_id_family_fallback"
        for row in rows
    )


@pytest.mark.parametrize(
    ("pg_id", "site_id", "category"),
    [
        ("EDU_9999", "3", "1"),
        ("JFAC9999", "3", ""),
    ],
)
def test_jongno_identity_fallback_keeps_conflicts_and_mixed_jfac_ambiguous(
    monkeypatch, pg_id: str, site_id: str, category: str
) -> None:
    page = _list_page(_card(pg_id, "접수중", "식별자 검증 강좌"), total_count=1, current=1)
    detail = _detail_page(pg_id, site_id, category, "접수중", "식별자 검증 강좌", "교육장")
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", _fake_fetcher({1: page}, lambda _pg_id: detail))

    rows, _parser, meta = municipal.collect_jongno_reserv_deadline(
        _target(), timeout=5, max_pages=1, detail_limit=10
    )

    assert rows == []
    assert meta["source_identity_errors"] == 1
    assert meta["source_identity_recovered_count"] == 0
    assert meta["pagination_complete"] is False
    assert "detail source identity was invalid for 1" in meta["configured_collection_error"]


def test_jongno_facility_source_is_audited_as_separately_owned(
    monkeypatch,
) -> None:
    pg_id = "FMC_JONGNO010000502"
    page = _list_page(
        _card(pg_id, "접수중", "시설 스포츠 강좌"),
        total_count=1,
        current=1,
    )
    detail = _detail_page(
        pg_id,
        "2",
        "1",
        "접수중",
        "시설 스포츠 강좌",
        "종로구민회관",
        application_url="",
    )
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        _fake_fetcher({1: page}, lambda _pg_id: detail),
    )

    rows, _parser, meta = municipal.collect_jongno_reserv_deadline(
        _target(),
        timeout=5,
        max_pages=1,
        detail_limit=10,
    )

    assert rows == []
    assert municipal.jongno_reserv_source_matches_pg_id("2", pg_id) is True
    assert meta["excluded_non_education_count"] == 1
    assert meta["source_identity_errors"] == 0
    assert meta["application_errors"] == 0
    assert meta["pagination_complete"] is True
    assert meta["no_current_data"] is True
    assert "configured_collection_error" not in meta


def test_jongno_expired_period_overrides_stale_open_status(monkeypatch) -> None:
    pg_id = "ACMS_999"
    page = _list_page(
        _card(pg_id, "접수중", "종료된 강좌"),
        total_count=1,
        current=1,
    )
    detail = _detail_page(
        pg_id,
        "1",
        "1",
        "접수중",
        "종료된 강좌",
        "자치회관",
        start="2020-01-01",
        end="2020-01-31",
    )
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        _fake_fetcher({1: page}, lambda _pg_id: detail),
    )

    rows, _parser, meta = municipal.collect_jongno_reserv_deadline(
        _target(),
        timeout=5,
        max_pages=1,
        detail_limit=10,
    )

    assert rows == []
    assert meta["expired_count"] == 1
    assert meta["pagination_complete"] is True
    assert meta["no_current_data"] is True


def test_jongno_undated_recurring_template_is_audited_and_excluded(
    monkeypatch,
) -> None:
    pg_id = "ACMS_998"
    page = _list_page(
        _card(pg_id, "접수중", "상시 헬스"),
        total_count=1,
        current=1,
    )
    detail = _detail_page(
        pg_id,
        "1",
        "1",
        "접수중",
        "상시 헬스",
        "자치회관",
        start="",
        end="",
    )
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        _fake_fetcher({1: page}, lambda _pg_id: detail),
    )

    rows, _parser, meta = municipal.collect_jongno_reserv_deadline(
        _target(),
        timeout=5,
        max_pages=1,
        detail_limit=10,
    )

    assert rows == []
    assert meta["undated_count"] == 1
    assert meta["pagination_complete"] is True
    assert meta["no_current_data"] is True
    assert "configured_collection_error" not in meta


@pytest.mark.parametrize(
    "url",
    [
        "http://www.jongno.go.kr/reserv/alllist.do?viewType=2",
        "https://jongno.go.kr/reserv/alllist.do?viewType=2",
        "https://evil.jongno.go.kr/reserv/alllist.do?viewType=2",
        "https://www.jongno.go.kr:443/reserv/alllist.do?viewType=2",
        "https://www.jongno.go.kr:bad/reserv/alllist.do?viewType=2",
        "https://www.jongno.go.kr/reserv/alllist.do?viewType=1",
        "https://www.jongno.go.kr/reserv/alllist.do?viewType=2&pageIndex=1",
        "https://www.jongno.go.kr/reserv/alllist.do?viewType=2#fragment",
    ],
)
def test_jongno_owner_route_is_exact(url: str) -> None:
    assert municipal.is_jongno_reserv_owned_url(url) is False


def test_jongno_canonical_dispatch_and_provider_are_exact(monkeypatch) -> None:
    assert municipal.is_jongno_reserv_owned_url(TARGET_URL) is True
    with pytest.raises(ValueError, match="canonical provider"):
        municipal.collect_jongno_reserv_deadline(
            _target(provider="MUNI_UNOWNED"), timeout=5, max_pages=50, detail_limit=500
        )
    sentinel = ([{"title": "sentinel"}], municipal.JONGNO_RESERV_PARSER, {"pages": 1})
    monkeypatch.setattr(municipal, "collect_jongno_reserv_deadline", lambda *_args, **_kwargs: sentinel)
    assert municipal.collect_from_url(
        _target(), timeout=5, max_depth=0, max_pages=50, detail_limit=500
    ) == sentinel


def test_jongno_page_cap_duplicate_and_count_mismatch_are_partial(monkeypatch) -> None:
    first = _list_page(_card("ACMS_1", "접수중", "첫 강좌"), total_count=7, current=1)
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        _fake_fetcher(
            {1: first},
            lambda pg_id: _detail_page(pg_id, "1", "7", "접수중", "첫 강좌", "교육장"),
        ),
    )
    rows, _parser, meta = municipal.collect_jongno_reserv_deadline(
        _target(), timeout=5, max_pages=1, detail_limit=10
    )
    assert len(rows) == 1
    assert meta["source_cap_reached"] is True
    assert meta["pagination_complete"] is False
    assert "max_pages cap reached after 1 of 2" in meta["configured_collection_error"]
    assert "parsed 1 unique pgId values of 7" in meta["configured_collection_error"]

    duplicate_page = _list_page(
        _card("ACMS_1", "접수중", "첫 강좌") + _card("ACMS_1", "접수중", "중복 강좌"),
        total_count=2,
        current=1,
    )
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        _fake_fetcher(
            {1: duplicate_page},
            lambda pg_id: _detail_page(pg_id, "1", "1", "접수중", "첫 강좌", "교육장"),
        ),
    )
    rows, _parser, meta = municipal.collect_jongno_reserv_deadline(
        _target(), timeout=5, max_pages=1, detail_limit=10
    )
    assert len(rows) == 1
    assert meta["duplicate_count"] == 1
    assert meta["pagination_complete"] is False
    assert "duplicated (1)" in meta["configured_collection_error"]


@pytest.mark.parametrize("failure", ["cap", "detail", "application"])
def test_jongno_detail_cap_failure_or_untrusted_application_is_partial(monkeypatch, failure: str) -> None:
    page = _list_page(
        _card("ACMS_1", "접수중", "첫 강좌") + _card("ACMS_2", "접수중", "둘째 강좌"),
        total_count=2,
        current=1,
    )

    def detail(pg_id: str) -> str:
        if failure == "detail" and pg_id == "ACMS_2":
            raise RuntimeError("fixture detail outage")
        app_url = "https://evil.example/apply" if failure == "application" and pg_id == "ACMS_2" else None
        return _detail_page(pg_id, "1", "1", "접수중", f"강좌 {pg_id}", "교육장", application_url=app_url)

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", _fake_fetcher({1: page}, detail))
    rows, _parser, meta = municipal.collect_jongno_reserv_deadline(
        _target(), timeout=5, max_pages=1, detail_limit=1 if failure == "cap" else 2
    )
    assert meta["pagination_complete"] is False
    assert meta["no_current_data"] is False
    if failure == "cap":
        assert len(rows) == 1
        assert meta["detail_enrichment_capped"] is True
        assert "detail enrichment capped at 1 of 2" in meta["configured_collection_error"]
    elif failure == "detail":
        assert len(rows) == 1
        assert meta["detail_errors"] == 1
        assert "detail fetch failed for 1" in meta["configured_collection_error"]
    else:
        assert len(rows) == 1
        assert meta["application_errors"] == 1
        assert "trusted application link was missing for 1" in meta["configured_collection_error"]


def test_jongno_partial_default_blocks_save_and_stale(monkeypatch) -> None:
    save_calls: list[list[dict[str, Any]]] = []
    stale_calls: list[tuple[Any, ...]] = []

    class FakeWriter:
        def __init__(self, provider: str) -> None:
            assert provider == PROVIDER

        def save_rows(self, rows: list[dict[str, Any]]) -> int:
            save_calls.append(rows)
            return len(rows)

    monkeypatch.setattr(municipal, "load_targets", lambda *_args, **_kwargs: [_target()])
    monkeypatch.setattr(
        municipal,
        "collect_from_url",
        lambda *_args, **_kwargs: (
            [{"provider": PROVIDER, "provider_course_id": "ACMS_1", "title": "부분 강좌", "branch": "종로구"}],
            municipal.JONGNO_RESERV_PARSER,
            {"pages": 1, "pagination_complete": False, "configured_collection_error": "max_pages cap reached"},
        ),
    )
    monkeypatch.setattr(
        municipal,
        "get_db_connection",
        lambda: (_ for _ in ()).throw(AssertionError("partial collection must not open a DB transaction")),
    )
    monkeypatch.setattr(municipal, "MunicipalDbWriter", FakeWriter)
    monkeypatch.setattr(municipal, "mark_stale_courses", lambda *args: stale_calls.append(args) or 0)

    reports = municipal.run(
        source="municipal",
        target_limit=None,
        per_target_limit=0,
        min_score=0,
        include_review=True,
        save_db=True,
        mark_stale=True,
        max_depth=0,
        max_pages=1,
        detail_limit=1,
        timeout=5,
    )
    assert reports[0].success is True
    assert reports[0].saved == 0
    assert save_calls == []
    assert stale_calls == []


def test_jongno_target_lock_override_and_duplicate_exclusion() -> None:
    lifelong = yaml.safe_load(
        (municipal.ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml").read_text(encoding="utf-8")
    )
    canonical = next(row for row in lifelong["targets"] if row["provider"] == PROVIDER)
    assert canonical["collection_category"] == "공공예약"
    assert canonical["domain_category"] == "교육·강좌"
    assert canonical["source_group"] == "municipal_reservation"
    assert canonical["service_group"] == "공공강좌"
    assert canonical["service_group_policy"] == "locked"
    assert canonical["full_snapshot_required"] is True

    arguments = list(generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[PROVIDER])
    assert arguments == [
        "--save-db", "--mark-stale", "--per-target-limit", "0",
        "--max-pages", "150", "--detail-limit", "800",
    ]
    parsed = generated.parse_args(["--provider", PROVIDER, *arguments])
    assert parsed.save_db is True
    assert parsed.mark_stale is True
    assert parsed.per_target_limit == 0
    assert parsed.allow_partial_save is False

    sports = yaml.safe_load(
        (municipal.ROOT / "config" / "crawl_targets" / "sports_facility.yaml").read_text(encoding="utf-8")
    )
    by_provider = {row["provider"]: row for row in sports["targets"]}
    for duplicate_provider in ("MUNI_JACHI_JONGNO_GO_KR_2BCA35FB", "MUNI_WWW_JONGNO_GO_KR_728A2F6A"):
        duplicate = by_provider[duplicate_provider]
        assert duplicate["collection_type"] == "duplicate"
        assert duplicate["duplicate_of"] == PROVIDER
        assert duplicate["superseded_by"] == PROVIDER
    assert by_provider["MUNI_LLE_JONGNO_GO_KR_CF61BBD0"]["crawler_status"] == "ready"
    assert by_provider["MUNI_WWW_IJONGNO_CO_KR_F9ED1CA5"]["crawler_status"] == "ready"

    registry = yaml.safe_load((municipal.ROOT / "config" / "generated_yaml_crawler_registry.yaml").read_text(encoding="utf-8"))
    registry_by_provider = {row["provider"]: row for row in registry["targets"]}
    assert PROVIDER not in registry_by_provider
    assert PROVIDER in aggregate.municipal_provider_names()
    assert not (municipal.ROOT / "Crawler" / "generated_yaml" / f"{PROVIDER}.py").exists()
    assert "MUNI_JACHI_JONGNO_GO_KR_2BCA35FB" not in registry_by_provider
    assert "MUNI_WWW_JONGNO_GO_KR_728A2F6A" not in registry_by_provider
    assert "MUNI_LLE_JONGNO_GO_KR_CF61BBD0" in registry_by_provider
    assert "MUNI_WWW_IJONGNO_CO_KR_F9ED1CA5" in registry_by_provider
