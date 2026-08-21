from __future__ import annotations

import importlib
import runpy
import sys
from pathlib import Path

import pytest
import requests
from bs4 import BeautifulSoup

from Crawler import Crawler_EducationExperience as education_experience
from Crawler import Crawler_Emart as emart
from Crawler import Crawler_EsongpaSportsCulture as esongpa
from Crawler import Crawler_Homeplus as homeplus
from Crawler import Crawler_Lotte as lotte
from Crawler import Crawler_Sahasilver as sahasilver
from Crawler import Crawler_SeongnamBaeumsoop as seongnam
from Crawler import Crawler_SeosanReservation as seosan


ROOT = Path(__file__).resolve().parents[1]


class _ClosingSession:
    def __init__(self) -> None:
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True


class _Driver:
    def __init__(self) -> None:
        self.quit_called = False

    def quit(self) -> None:
        self.quit_called = True


@pytest.mark.parametrize(
    ("module_name", "provider"),
    [
        ("Crawler_AnyangLearning.py", "ANYANG_LIFELONG_LEARNING"),
        ("Crawler_BabsangWelfare.py", "BABSANG_WELFARE_PROGRAM"),
        ("Crawler_YonginLifelong.py", "YONGIN_LIFELONG_LEARNING"),
    ],
)
def test_manual_wrapper_keeps_provider_and_propagates_cli_exit(module_name: str, provider: str) -> None:
    source = (ROOT / "Crawler" / module_name).read_text(encoding="utf-8")
    assert f'PROVIDER = "{provider}"' in source
    assert "raise SystemExit(run_cli(PROVIDER))" in source
    module = importlib.import_module(f"Crawler.{Path(module_name).stem}")
    assert module.PROVIDER == provider


@pytest.mark.parametrize(
    ("module_name", "provider"),
    [
        ("Crawler_AnyangLearning.py", "ANYANG_LIFELONG_LEARNING"),
        ("Crawler_BabsangWelfare.py", "BABSANG_WELFARE_PROGRAM"),
        ("Crawler_YonginLifelong.py", "YONGIN_LIFELONG_LEARNING"),
    ],
)
def test_manual_wrapper_returns_run_cli_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    provider: str,
) -> None:
    from Crawler.generated_yaml import manual_generic_crawler

    captured: list[str] = []
    monkeypatch.setattr(
        manual_generic_crawler,
        "run_cli",
        lambda actual_provider: captured.append(actual_provider) or 23,
    )
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(ROOT / "Crawler" / module_name), run_name="__main__")
    assert raised.value.code == 23
    assert captured == [provider]


def test_busan_wrapper_uses_generated_provider_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from Crawler import Crawler_GeneratedYamlTargets as generated

    captured: list[str] = []
    monkeypatch.setattr(
        generated,
        "main",
        lambda args: captured.extend(args) or 29,
    )
    monkeypatch.setattr(sys, "argv", ["Crawler_BusanReservation.py"])
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(
            str(ROOT / "Crawler" / "Crawler_BusanReservation.py"),
            run_name="__main__",
        )
    assert raised.value.code == 29
    assert captured == ["--provider", "BUSAN_RESERVATION"]


def test_education_experience_restores_process_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    def fake_main() -> int:
        captured.extend(sys.argv)
        return 37

    original = ["crawler", "--limit", "1"]
    monkeypatch.setattr(education_experience, "main", fake_main)
    monkeypatch.setattr(sys, "argv", original[:])

    assert education_experience.run() == 37
    assert sys.argv == original
    for provider in education_experience.PROVIDERS:
        assert provider in captured


def test_education_experience_respects_equals_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []
    original = ["crawler", "--provider=CUSTOM", "--limit", "1"]
    monkeypatch.setattr(education_experience, "main", lambda: captured.extend(sys.argv) or 0)
    monkeypatch.setattr(sys, "argv", original[:])

    assert education_experience.run() == 0
    assert captured == original
    assert sys.argv == original


def test_education_experience_selects_explicit_mixed_and_url_targets() -> None:
    targets = [
        {
            "provider": "EXPLICIT_EXPERIENCE",
            "service_group": "체험",
            "url": "https://example.test/programs",
        },
        {
            "provider": "MIXED_EVENT",
            "service_group": "공공강좌",
            "domain_category": "체험·견학",
            "url": "https://example.test/reservations",
        },
        {
            "provider": "PERFORMANCE_VENUE",
            "service_group": "공공강좌",
            "name": "시립 공연장",
            "url": "https://example.test/reservations",
        },
        {
            "provider": "EXPERIENCE_ENDPOINT",
            "service_group": "공공강좌",
            "url": "https://example.test/yeyak/exprn/selectExprnList.do",
        },
        {
            "provider": "ORDINARY_COURSE",
            "service_group": "공공강좌",
            "domain_category": "평생학습",
            "url": "https://example.test/lectures",
        },
        {
            "provider": "ULSAN_EDU_BOOKING",
            "service_group": "공공강좌",
            "domain_category": "평생학습",
            "branch": "울산광역시교육청 통합예약",
            "url": "https://use.go.kr/booking/user/reservation/Edu/BD_selectReservationMngList.do",
        },
    ]

    assert education_experience.experience_provider_names(targets) == [
        "EXPERIENCE_ENDPOINT",
        "EXPLICIT_EXPERIENCE",
        "MIXED_EVENT",
        "PERFORMANCE_VENUE",
        "ULSAN_EDU_BOOKING",
    ]


def test_education_experience_excludes_municipal_aggregate_owners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        education_experience.generated_targets,
        "MUNICIPAL_OPERATIONAL_PROVIDER_NAMES",
        {"AGGREGATE_LIBRARY"},
    )
    targets = [
        {
            "provider": "AGGREGATE_LIBRARY",
            "source_group": "museum",
            "url": "https://example.test/library",
        },
        {
            "provider": "DIRECT_LIBRARY",
            "source_group": "museum",
            "url": "https://example.test/direct-library",
        },
    ]
    assert education_experience.experience_provider_names(targets) == ["DIRECT_LIBRARY"]


def test_education_experience_fails_closed_when_dynamic_registry_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ["crawler", "--limit", "1"]
    monkeypatch.setattr(education_experience, "experience_provider_names", lambda: [])
    monkeypatch.setattr(
        education_experience,
        "main",
        lambda: pytest.fail("generated crawler must not run without a target"),
    )
    monkeypatch.setattr(sys, "argv", original[:])

    assert education_experience.run() == 2
    assert sys.argv == original


def test_education_experience_filters_non_experience_siblings_from_mixed_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = [
        {
            "provider": "MIXED",
            "service_group": "체험",
            "url": "https://example.test/experience",
        },
        {
            "provider": "MIXED",
            "service_group": "공공강좌",
            "domain_category": "평생학습",
            "url": "https://example.test/lecture",
        },
    ]
    loaded: list[dict] = []

    def fixture_loader(*_args, **_kwargs):
        return targets

    monkeypatch.setattr(education_experience, "experience_provider_names", lambda: ["MIXED"])
    monkeypatch.setattr(education_experience.generated_targets, "load_yaml_targets", fixture_loader)
    monkeypatch.setattr(
        education_experience,
        "main",
        lambda: loaded.extend(education_experience.generated_targets.load_yaml_targets()) or 0,
    )
    monkeypatch.setattr(sys, "argv", ["crawler"])

    assert education_experience.run() == 0
    assert [target["url"] for target in loaded] == ["https://example.test/experience"]
    assert education_experience.generated_targets.load_yaml_targets is fixture_loader


def test_education_experience_prepares_main_site_discovery_without_mutating_registry() -> None:
    target = {
        "provider": "EXPERIENCE",
        "service_group": "체험",
        "url": "https://museum.example.test/reservation/program/list.do?category=1",
    }

    prepared = education_experience.prepare_experience_target(target)

    assert prepared is not target
    assert "main_url" not in target
    assert prepared["main_url"] == "https://museum.example.test/"
    assert prepared["discover_from_main_url"] is True
    assert prepared["main_discovery_max_pages"] == 4
    assert prepared["main_discovery_max_candidates"] == 12


def test_education_experience_prefers_explicit_main_url() -> None:
    target = {
        "url": "https://reservation.example.test/program/list",
        "main_url": "https://www.example.test/culture/",
    }

    assert education_experience.experience_main_url(target) == "https://www.example.test/culture/"


def test_verified_experience_parser_keeps_the_configured_ownership_boundary() -> None:
    target = {
        "provider": "SUWON_LIBRARY_MA",
        "service_group": "체험",
        "url": "https://www.suwonlib.go.kr/reserve/lecture/lectureList.do",
        "last_quality": {"parser": "suwon_library_lecture_list"},
    }

    prepared = education_experience.prepare_experience_target(target)

    assert prepared["discover_from_main_url"] is False
    assert "main_url" not in prepared


@pytest.mark.parametrize(
    ("validator", "good", "evil"),
    [
        (
            homeplus._trusted_homeplus_url,
            "https://mschool.homeplus.co.kr/Lecture/Detail?id=1",
            "https://evil.example/Lecture/Detail?id=1",
        ),
        (
            emart._trusted_emart_site_url,
            "https://www.cultureclub.emart.com/class/abc",
            "https://www.cultureclub.emart.com.evil.example/class/abc",
        ),
        (
            lotte._trusted_lotte_url,
            "https://culture.lotteshopping.com/application/search/view.do?lectCd=1",
            "javascript:alert(1)",
        ),
        (
            sahasilver.trusted_source_url,
            "https://www.sahasilver.org/05/02.php?uid=1",
            "https://evil.example/05/02.php?uid=1",
        ),
        (
            seosan.trusted_source_url,
            "https://total.seosan.go.kr/total/selectEdcAtrCourseListU.do?key=1",
            "https://total.seosan.go.kr.evil.example/total/selectEdcAtrCourseListU.do?key=1",
        ),
    ],
)
def test_manual_crawler_origin_guards(validator, good: str, evil: str) -> None:
    assert validator(good) == good
    assert validator(evil) == ""


def test_emart_api_key_can_only_reach_the_pinned_appsync_endpoint() -> None:
    assert emart._trusted_graphql_endpoint(emart.EMART_GRAPHQL_ENDPOINT)
    assert emart._trusted_graphql_endpoint("https://evil.example/graphql") == ""
    assert (
        emart._trusted_graphql_endpoint(
            "https://tjcdarnuonge5epm44y2nvckk4.appsync-api.ap-northeast-2.amazonaws.com/other"
        )
        == ""
    )


def test_seosan_pagination_fixture_discovers_only_same_origin_list_pages() -> None:
    soup = BeautifulSoup(
        """
        <a href="?key=326&pageIndex=2">2</a>
        <a href="https://evil.example/total/selectEdcAtrCourseListU.do?pageIndex=3">3</a>
        <a href="./selectEdcAtrCourseViewU.do?key=326&edcCourseNo=10">course</a>
        """,
        "html.parser",
    )
    page_url = seosan.LIST_URLS[0]
    assert seosan.pagination_links_from_list(soup, page_url) == [
        "https://total.seosan.go.kr/total/selectEdcAtrCourseListU.do?key=326&pageIndex=2"
    ]
    assert seosan.detail_links_from_list(soup, page_url) == [
        "https://total.seosan.go.kr/total/selectEdcAtrCourseViewU.do?key=326&edcCourseNo=10"
    ]


def test_sahasilver_list_fixture_rejects_unknown_branch_and_external_links() -> None:
    soup = BeautifulSoup(
        """
        <table>
          <tr><td>1</td><td>unknown</td><td></td><td>teacher</td><td>10</td><td>접수</td>
            <td class="board-list-title"><a href="https://evil.example/05/02.php?uid=1">bad</a></td></tr>
          <tr><td>2</td><td>신평</td><td></td><td>teacher</td><td>10</td><td>접수</td>
            <td class="board-list-title"><a href="?uid=2">valid course</a></td></tr>
        </table>
        """,
        "html.parser",
    )
    rows, _links = sahasilver.parse_list_page(soup, sahasilver.LIST_URL)
    assert len(rows) == 1
    assert rows[0]["provider_course_id"] == "2"
    assert rows[0]["raw_url"].startswith(sahasilver.BASE_URL)


def test_esongpa_row_fixture_requires_identity_and_normalizes_period() -> None:
    valid = esongpa.row_to_course(
        {
            "classCd": "C1",
            "classNm": "swimming",
            "comcd": "SONGPA01",
            "grpcd": {"classSdate": "20260701", "classEdate": "20260731"},
        },
        "20260710",
    )
    assert valid is not None
    assert valid["period"] == "2026-07-01 ~ 2026-07-31"
    assert esongpa.row_to_course({"classNm": "missing code"}, "20260710") is None


def test_seongnam_date_parser_rejects_impossible_dates() -> None:
    assert seongnam.parse_short_date("26.07.10") == "2026-07-10"
    assert seongnam.parse_short_date("2026.99.99") == ""


def test_emart_graphql_fixture_requires_bounded_class_identity() -> None:
    crawler = emart.EmartCrawler.__new__(emart.EmartCrawler)
    crawler.base_url = "https://www.cultureclub.emart.com"
    valid = crawler._course_data_from_graphql(
        {"classId": "class-1", "classTitle": "pottery", "classDateInfo": {}},
        "branch-id",
        "store-1",
    )
    assert valid is not None
    assert valid["raw_url"] == "https://www.cultureclub.emart.com/class/class-1"
    assert (
        crawler._course_data_from_graphql(
            {"classId": "../escape", "classTitle": "bad", "classDateInfo": {}},
            "branch-id",
            "store-1",
        )
        is None
    )


@pytest.mark.parametrize(
    "call",
    [
        lambda: sahasilver.collect(0, 20, 5),
        lambda: seosan.collect(1, 0, 5),
        lambda: esongpa.collect(1, 20, 0, 5),
        lambda: seongnam.collect(limit=1, office_limit=None, max_pages=0, timeout=20, detail=False),
    ],
)
def test_manual_collectors_reject_unbounded_or_nonpositive_options(call) -> None:
    with pytest.raises(ValueError):
        call()


@pytest.mark.parametrize("module", [sahasilver, seosan])
def test_html_collectors_close_sessions_on_success(monkeypatch: pytest.MonkeyPatch, module) -> None:
    closing = _ClosingSession()
    monkeypatch.setattr(module, "session", lambda: closing)
    monkeypatch.setattr(module, "fetch_soup", lambda *_args, **_kwargs: BeautifulSoup("", "html.parser"))

    if module is sahasilver:
        rows, _meta = module.collect(None, 20, 5)
    else:
        rows, _meta = module.collect(None, 20, 1)
    assert rows == []
    assert closing.closed is True


def test_json_collector_closes_session_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    closing = _ClosingSession()
    monkeypatch.setattr(esongpa, "session", lambda: closing)
    monkeypatch.setattr(esongpa, "init_session", lambda *_args: "20260710")
    monkeypatch.setattr(esongpa, "post_json", lambda *_args: {"resultList": [], "totalCount": 0})

    rows, meta = esongpa.collect(None, 20, 100, 5)
    assert rows == []
    assert meta["complete"] is True
    assert closing.closed is True


def test_seongnam_empty_discovery_still_closes_session(monkeypatch: pytest.MonkeyPatch) -> None:
    closing = _ClosingSession()
    monkeypatch.setattr(seongnam, "session", lambda: closing)
    monkeypatch.setattr(seongnam, "discover_offices_from_files", lambda: [])

    rows, meta = seongnam.collect(
        limit=None,
        office_limit=None,
        max_pages=5,
        timeout=20,
        detail=False,
    )
    assert rows == []
    assert meta["complete"] is True
    assert closing.closed is True


def test_homeplus_http_retry_is_bounded() -> None:
    class FlakySession:
        def __init__(self) -> None:
            self.calls = 0

        def request(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise requests.Timeout("temporary")
            return type("Response", (), {"raise_for_status": lambda self: None})()

        def close(self) -> None:
            return None

    crawler = homeplus.HomeplusCrawler.__new__(homeplus.HomeplusCrawler)
    crawler.session = FlakySession()
    response = crawler._request_with_retry("GET", homeplus.HomeplusCrawler.__name__, timeout=1)
    assert response is not None
    assert crawler.session.calls == 2


def test_homeplus_api_list_fixture_requires_real_identity_and_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crawler = homeplus.HomeplusCrawler(use_selenium=False)
    monkeypatch.setattr(
        crawler,
        "_resolve_store_info",
        lambda **_kwargs: {"StoreCode": "S1", "StoreName": "branch"},
    )
    monkeypatch.setattr(crawler, "save_branch", lambda _row: "branch-id")
    item = BeautifulSoup(
        """
        <li id="liLecture_C1">
          <span class="office_name">branch</span>
          <span class="title_1">category</span>
          <span class="title_2">course title</span>
          <span class="sub_info_wrap"><span class="sub_txt">10:00 ~ 11:00</span></span>
        </li>
        """,
        "html.parser",
    ).li
    try:
        parsed = crawler._parse_course_item(item, {})
        assert parsed is not None
        assert parsed["provider_course_id"] == "S1:C1"
        assert parsed["title"] == "course title"

        invalid = BeautifulSoup('<li id="liLecture_bad/id"></li>', "html.parser").li
        assert crawler._parse_course_item(invalid, {}) is None
    finally:
        crawler.close()


def test_homeplus_network_failure_closes_session(monkeypatch: pytest.MonkeyPatch) -> None:
    crawler = homeplus.HomeplusCrawler(use_selenium=False)
    session = _ClosingSession()
    crawler.session.close()
    crawler.session = session
    monkeypatch.setattr(
        crawler,
        "scrape_courses_api",
        lambda **_kwargs: (_ for _ in ()).throw(requests.Timeout("offline")),
    )

    assert crawler.run(limit=1) is False
    assert session.closed is True


def test_homeplus_pagination_proves_total_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    crawler = homeplus.HomeplusCrawler(use_selenium=False)

    class Response:
        def __init__(self, text: str = "") -> None:
            self.text = text

    def response_for(_method: str, _url: str, **kwargs):
        if "data" not in kwargs:
            return Response()
        page = kwargs["data"]["page"]
        return Response(f'<div id="divTotalCnt">2</div><li id="liLecture_{page}"></li>')

    monkeypatch.setenv("HOMEPLUS_MAX_PAGES", "5")
    monkeypatch.setenv("HOMEPLUS_DETAIL_LIMIT", "0")
    monkeypatch.setattr(
        crawler,
        "_get_store_lookup",
        lambda: {"0001": {"StoreCode": "0001", "StoreName": "Test branch"}},
    )
    monkeypatch.setattr(crawler, "_request_with_retry", response_for)
    monkeypatch.setattr(
        crawler,
        "_parse_course_item",
        lambda item, _cache: {
            "provider": "HOMEPLUS",
            "branch_code": "0001",
            "provider_course_id": item["id"],
            "title": item["id"],
            "raw_url": f"https://mschool.homeplus.co.kr/Lecture/Detail?id={item['id']}",
        },
    )
    monkeypatch.setattr(crawler, "apply_branch_reception_period", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(crawler, "save_course", lambda _row: True)
    try:
        assert crawler.scrape_courses_api() == 2
        assert crawler.crawl_complete is True
    finally:
        crawler.close()


def test_homeplus_duplicate_page_cannot_claim_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    crawler = homeplus.HomeplusCrawler(use_selenium=False)

    class Response:
        def __init__(self, text: str = "") -> None:
            self.text = text

    monkeypatch.setenv("HOMEPLUS_MAX_PAGES", "5")
    monkeypatch.setenv("HOMEPLUS_DETAIL_LIMIT", "0")
    monkeypatch.setattr(
        crawler,
        "_get_store_lookup",
        lambda: {"0001": {"StoreCode": "0001", "StoreName": "Test branch"}},
    )
    monkeypatch.setattr(
        crawler,
        "_request_with_retry",
        lambda _method, _url, **kwargs: Response(
            '<div id="divTotalCnt">2</div><li id="liLecture_1"></li>' if "data" in kwargs else ""
        ),
    )
    monkeypatch.setattr(
        crawler,
        "_parse_course_item",
        lambda *_args: {
            "provider": "HOMEPLUS",
            "branch_code": "0001",
            "provider_course_id": "same",
            "title": "same",
            "raw_url": "https://mschool.homeplus.co.kr/Lecture/Detail?id=same",
        },
    )
    monkeypatch.setattr(crawler, "apply_branch_reception_period", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(crawler, "save_course", lambda _row: True)
    try:
        assert crawler.scrape_courses_api() == 1
        assert crawler.crawl_complete is False
    finally:
        crawler.close()


@pytest.mark.parametrize("crawler_type", [homeplus.HomeplusCrawler, emart.EmartCrawler])
def test_browser_crawler_close_is_idempotent(crawler_type) -> None:
    crawler = crawler_type.__new__(crawler_type)
    driver = _Driver()
    session = _ClosingSession()
    crawler.driver = driver
    if crawler_type is homeplus.HomeplusCrawler:
        crawler.session = session
    else:
        crawler.http_session = session

    crawler.close()
    crawler.close()
    assert driver.quit_called is True
    assert session.closed is True
    assert crawler.driver is None


def test_emart_constructor_defers_selenium_until_browser_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        emart.EmartCrawler,
        "_init_driver",
        lambda _self: calls.append("started"),
    )

    crawler = emart.EmartCrawler()
    try:
        assert crawler.driver is None
        assert crawler.wait is None
        assert calls == []
    finally:
        crawler.close()


def test_lotte_invalid_limit_still_cleans_up() -> None:
    crawler = lotte.LotteCrawler()
    driver = _Driver()
    session = _ClosingSession()
    crawler.driver = driver
    crawler.http_session.close()
    crawler.http_session = session

    with pytest.raises(ValueError):
        crawler.run(limit=0)
    assert driver.quit_called is True
    assert session.closed is True


def test_emart_invalid_limit_still_cleans_up() -> None:
    crawler = emart.EmartCrawler.__new__(emart.EmartCrawler)
    driver = _Driver()
    session = _ClosingSession()
    crawler.driver = driver
    crawler.http_session = session

    with pytest.raises(ValueError):
        crawler.run(limit=0)
    assert driver.quit_called is True
    assert session.closed is True


def test_lotte_page_retry_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    crawler = lotte.LotteCrawler()
    calls: list[int] = []
    closes: list[int] = []
    monkeypatch.setattr(crawler, "_get_page_once", lambda *_args, **_kwargs: calls.append(1) or None)
    monkeypatch.setattr(crawler, "_close_driver", lambda: closes.append(1))
    try:
        assert crawler._get_page("https://culture.lotteshopping.com/index.do", wait_time=0) is None
        assert len(calls) == 2
        assert len(closes) == 2
        assert crawler.had_errors is True
        assert crawler.crawl_complete is False
    finally:
        crawler.http_session.close()


def test_esongpa_cli_fails_closed_without_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", "--limit", "1"])
    monkeypatch.setattr(esongpa, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.SSLError("bad cert")))
    assert esongpa.main() == 1


@pytest.mark.parametrize(
    ("module", "writer_name"),
    [
        (sahasilver, "SahasilverDbWriter"),
        (seosan, "SeosanDbWriter"),
    ],
)
def test_html_crawler_stale_cutoff_is_crawl_start(
    monkeypatch: pytest.MonkeyPatch,
    module,
    writer_name: str,
) -> None:
    marker = object()
    captured: list[object] = []

    class Writer:
        def __init__(self, _provider: str) -> None:
            pass

        def save_rows(self, rows) -> int:
            return len(rows)

    monkeypatch.setattr(sys, "argv", ["crawler", "--save-db", "--mark-stale"])
    monkeypatch.setattr(module, "utc_now", lambda: marker)
    monkeypatch.setattr(module, "collect", lambda *_args: ([{"title": "course"}], {"complete": True}))
    monkeypatch.setattr(module, writer_name, Writer)
    monkeypatch.setattr(module, "mark_stale_courses", lambda _provider, cutoff: captured.append(cutoff))
    monkeypatch.setattr(module, "write_report", lambda *_args: Path("report.yaml"))
    monkeypatch.setattr(module, "print_quality", lambda *_args: None)

    assert module.main() == 0
    assert captured == [marker]


def test_esongpa_partial_crawl_never_marks_rows_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    class Writer:
        def __init__(self, _provider: str) -> None:
            pass

        def save_rows(self, rows) -> int:
            return len(rows)

    monkeypatch.setattr(esongpa, "collect", lambda **_kwargs: ([{"title": "course"}], {"complete": False}))
    monkeypatch.setattr(esongpa, "EsongpaDbWriter", Writer)
    monkeypatch.setattr(
        esongpa,
        "mark_stale_courses",
        lambda *_args: pytest.fail("partial crawl must not mark stale rows"),
    )
    monkeypatch.setattr(esongpa, "write_report", lambda *_args: Path("report.yaml"))
    monkeypatch.setattr(esongpa, "print_summary", lambda *_args: None)

    with pytest.raises(RuntimeError, match="stale cleanup refused"):
        esongpa.run(None, True, True, 20, 100, 5)


def test_seongnam_stale_cutoff_is_crawl_start(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = object()
    captured: list[object] = []
    monkeypatch.setattr(seongnam, "utc_now", lambda: marker)
    monkeypatch.setattr(seongnam, "collect", lambda **_kwargs: ([{"title": "course"}], {"complete": True}))
    monkeypatch.setattr(seongnam, "save_db", lambda rows: len(rows))
    monkeypatch.setattr(seongnam, "mark_stale_courses", lambda _provider, cutoff: captured.append(cutoff))
    monkeypatch.setattr(seongnam, "write_report", lambda *_args: Path("report.yaml"))
    monkeypatch.setattr(seongnam, "print_summary", lambda *_args: None)

    rows = seongnam.run(
        limit=None,
        save=True,
        mark_stale=True,
        office_limit=None,
        max_pages=5,
        timeout=20,
        detail=False,
    )
    assert rows == [{"title": "course"}]
    assert captured == [marker]


@pytest.mark.parametrize(
    "relative_path",
    [
        "Crawler/Crawler_Emart.py",
        "Crawler/Crawler_Homeplus.py",
        "Crawler/Crawler_Lotte.py",
        "Crawler/Crawler_EsongpaSportsCulture.py",
        "Crawler/Crawler_Sahasilver.py",
        "Crawler/Crawler_SeongnamBaeumsoop.py",
        "Crawler/Crawler_SeosanReservation.py",
    ],
)
def test_dedicated_crawlers_keep_transport_security_contract(relative_path: str) -> None:
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    assert "verify=False" not in source
    assert "--no-sandbox" not in source
    assert "webdriver.Chrome(" not in source
    assert "mark_stale_courses(PROVIDER, utc_now())" not in source


@pytest.mark.parametrize(
    ("module", "crawler_type", "needs_branch_id"),
    [
        (emart, emart.EmartCrawler, False),
        (homeplus, homeplus.HomeplusCrawler, False),
        (lotte, lotte.LotteCrawler, True),
    ],
)
def test_browser_course_writes_execute_payload_sanitization_before_db_work(
    monkeypatch: pytest.MonkeyPatch,
    module,
    crawler_type,
    needs_branch_id: bool,
) -> None:
    sanitized: list[dict] = []
    payload = {"title": "unsafe", "raw_url": "javascript:alert(1)"}

    def reject_after_recording(course: dict) -> None:
        sanitized.append(course)
        raise ValueError("unsafe payload")

    monkeypatch.setattr(module, "sanitize_course_payload", reject_after_recording)
    crawler = crawler_type.__new__(crawler_type)
    result = crawler.save_course(payload, "branch-id") if needs_branch_id else crawler.save_course(payload)

    assert result is False
    assert sanitized == [payload]
