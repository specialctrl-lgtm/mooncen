from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
import requests
from bs4 import BeautifulSoup

import run_crawlers
from Crawler import Crawler_Emart as emart
from Crawler import Crawler_MunicipalYaml as municipal
from ops_agent import crawler_worker


def _ansan_target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider="MUNI_RESERVE_ANSAN_GO_KR_02253999",
        name="Ansan reservation",
        branch="Ansan",
        url="https://reserve.ansan.go.kr/exp/X01/expList.do?currentMenuNo=667",
        source="test",
    )


def _ansan_page(category_code: str, page: int) -> BeautifulSoup:
    if page > 1:
        return BeautifulSoup('<ul class="blog reserv"></ul>', "lxml")
    return BeautifulSoup(
        f"""
        <ul class="blog reserv">
          <li>
            <a onclick="fnView('{category_code}-1')"></a>
            <div class="txtW">
              <strong class="tit">{category_code} program</strong>
              <ul class="etc">
                <li><span class="em">기관/부서</span>Test department</li>
                <li><span class="em">접수기간</span>2026.07.01 ~ 2026.08.01</li>
                <li><span class="em">체험/견학기간</span>2026.08.02 ~ 2026.08.03</li>
                <li><span class="em">대상</span>전체</li>
                <li><span class="em">위치</span>Test venue</li>
              </ul>
            </div>
            <span class="label">접수중</span>
            <div class="addLabel"><span>무료</span></div>
          </li>
        </ul>
        <a href="?pageIndex=2">next</a>
        """,
        "lxml",
    )


def test_ansan_experience_marks_complete_category_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(municipal, "session", lambda: object())

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 5
        parsed = urlparse(url)
        category_code = parsed.path.split("/")[2]
        page = int((parse_qs(parsed.query).get("pageIndex") or ["1"])[0])
        return _ansan_page(category_code, page)

    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    rows, parser, meta = municipal.collect_ansan_experience_categories(
        _ansan_target(),
        timeout=5,
        max_pages=10,
    )

    assert parser == "ansan_experience_category_cards"
    assert len(rows) == len(municipal.ANSAN_EXPERIENCE_CATEGORIES)
    assert meta["categories_completed"] == meta["categories_expected"] == 3
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_cap_reached"] is False


def test_ansan_experience_does_not_claim_complete_at_page_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda _session, url, timeout: _ansan_page(urlparse(url).path.split("/")[2], 1),
    )

    _rows, _parser, meta = municipal.collect_ansan_experience_categories(
        _ansan_target(),
        timeout=5,
        max_pages=1,
    )

    assert meta["pagination_complete"] is False
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True


def test_dobong_rate_limit_uses_retry_after_and_paces_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = requests.Response()
    response.status_code = 429
    response.headers["Retry-After"] = "3"
    rate_limited = requests.HTTPError("rate limited", response=response)
    calls = 0
    sleeps: list[float] = []

    def fake_fetch(_session: object, _url: str, timeout: int) -> BeautifulSoup:
        nonlocal calls
        assert timeout == 5
        calls += 1
        if calls == 1:
            raise rate_limited
        return BeautifulSoup("<html><body>ok</body></html>", "lxml")

    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
    monkeypatch.setattr(municipal.time, "sleep", sleeps.append)

    soup = municipal.fetch_dobong_soup(
        object(),
        "https://yeyak.dobongsiseol.or.kr/lecture/index.php",
        timeout=5,
    )

    assert soup.get_text(strip=True) == "ok"
    assert calls == 2
    assert sleeps == [3.0, municipal.DOBONG_REQUEST_PACE_SECONDS]


@pytest.mark.parametrize(
    ("anchor_period", "title", "target", "expected"),
    [
        (
            "2026-08-01 ~ 2026-08-31",
            "수영",
            "성인 1개월",
            "2026-08-01 ~ 2026-08-31",
        ),
        (
            "2026-08-01 ~ 2026-08-31",
            "헬스 12개월",
            "성인",
            "2026-08-01 ~ 2027-07-31",
        ),
        (
            "2026-12-01 ~ 2026-12-31",
            "수영 3개월",
            "성인",
            "2026-12-01 ~ 2027-02-28",
        ),
        (
            "2026-08-01 ~ 2026-10-31",
            "정규 강좌",
            "성인",
            "2026-08-01 ~ 2026-10-31",
        ),
    ],
)
def test_dobong_period_is_derived_from_detail_anchor_and_duration(
    anchor_period: str,
    title: str,
    target: str,
    expected: str,
) -> None:
    assert municipal.dobong_period_from_anchor(
        anchor_period,
        title,
        target,
    ) == expected


def test_dobong_collection_fetches_detail_once_per_event_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = municipal.CrawlTarget(
        provider="MUNI_DOBONGSISEOL_OR_KR_LECTURE",
        name="Dobong facilities lectures",
        branch="Dobong",
        url="https://yeyak.dobongsiseol.or.kr/lecture/index.php?n_type=lecture",
        source="test",
    )
    page = BeautifulSoup(
        """
        <h2>창동문화체육센터</h2>
        <table>
          <tbody>
            <tr>
              <td>수영 3개월</td>
              <td>홍길동</td>
              <td>-</td>
              <td>월수금 09:00~09:50</td>
              <td>성인</td>
              <td>90,000원</td>
              <td>20명</td>
              <td>접수가능</td>
              <td>
                <a onclick="goLink('01','008','SWIM','123','SALE')">상세</a>
              </td>
            </tr>
            <tr>
              <td>수영 3개월</td>
              <td>홍길동</td>
              <td>-</td>
              <td>월수금 10:00~10:50</td>
              <td>성인 3개월</td>
              <td>270,000원</td>
              <td>20명</td>
              <td>접수가능</td>
              <td>
                <a onclick="goLink('01','008','SWIM','124','SALE')">상세</a>
              </td>
            </tr>
          </tbody>
        </table>
        """,
        "lxml",
    )
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "dobong_lecture_entry_urls",
        lambda _session, _target, timeout: [target.url],
    )
    monkeypatch.setattr(
        municipal,
        "fetch_dobong_soup",
        lambda _session, _url, timeout: page,
    )
    detail_calls: list[dict[str, str]] = []

    def fake_detail(
        _session: object,
        _url: str,
        params: dict[str, str],
        _timeout: int,
    ) -> dict[str, str]:
        detail_calls.append(params)
        return {
            "period": "2026-08-01 ~ 2026-08-31",
            "apply_period": "2026-07-20 ~ 2026-07-31",
        }

    monkeypatch.setattr(
        municipal,
        "dobong_detail_fields",
        fake_detail,
    )

    rows, parser, meta = municipal.collect_dobongsiseol_lecture(
        target,
        timeout=5,
        max_pages=10,
        detail_limit=100,
    )

    assert parser == "dobongsiseol_lecture_table"
    assert len(rows) == 2
    assert [row["period"] for row in rows] == [
        "2026-08-01 ~ 2026-10-31",
        "2026-08-01 ~ 2026-10-31",
    ]
    assert len(detail_calls) == 1
    assert meta["detail_pages"] == 1
    assert meta["detail_groups"] == 1
    assert meta["detail_group_cache_hits"] == 1
    assert meta["snapshot_complete"] is True


def test_emart_default_store_catalogue_is_validated_and_deduplicated() -> None:
    crawler = emart.EmartCrawler.__new__(emart.EmartCrawler)
    response = SimpleNamespace(
        json=lambda: [
            {
                "storeListInfo": [
                    {"storeCode": "100", "storeName": "KINTEX", "storeNickName": "KINTEX"},
                    {"storeCode": "100", "storeName": "duplicate"},
                    {"storeCode": "200", "storeName": "Second"},
                ]
            }
        ]
    )
    crawler._http_request_with_retry = lambda *_args, **_kwargs: response

    branches = crawler._fetch_default_branches()

    assert [(row["branch_code"], row["name"]) for row in branches] == [
        ("100", "KINTEX점"),
        ("200", "Second점"),
    ]


def test_emart_discovers_public_graphql_key_only_from_the_pinned_frontend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(emart, "EMART_GRAPHQL_API_KEY", "")
    crawler = emart.EmartCrawler.__new__(emart.EmartCrawler)
    crawler._graphql_api_key_cache = ""
    crawler._graphql_api_key_resolved = False
    api_key = "da2-" + ("A" * 24)
    calls: list[str] = []
    responses = {
        emart.EMART_FRONTEND_URL: (
            b'<script src="https://evil.example/static/js/main.badbad.chunk.js"></script>'
            b'<script src="/static/js/main.abc12345.chunk.js"></script>'
        ),
        "https://www.cultureclub.emart.com/static/js/main.abc12345.chunk.js": (
            f'{{"awsappsyncgraphqlEndpoint":"{emart.EMART_GRAPHQL_ENDPOINT}",'
            f'"awsappsyncapiKey":"{api_key}"}}'
        ).encode(),
    }

    def request(_method: str, url: str, **_kwargs):
        calls.append(url)
        return SimpleNamespace(content=responses[url])

    crawler._http_request_with_retry = request

    assert crawler._resolve_graphql_api_key() == api_key
    assert crawler._resolve_graphql_api_key() == api_key
    assert calls == [
        emart.EMART_FRONTEND_URL,
        "https://www.cultureclub.emart.com/static/js/main.abc12345.chunk.js",
    ]


def test_emart_rejects_public_bundle_with_an_unapproved_graphql_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(emart, "EMART_GRAPHQL_API_KEY", "")
    crawler = emart.EmartCrawler.__new__(emart.EmartCrawler)
    crawler._graphql_api_key_cache = ""
    crawler._graphql_api_key_resolved = False
    responses = iter(
        [
            SimpleNamespace(content=b'<script src="/static/js/main.abc12345.chunk.js"></script>'),
            SimpleNamespace(
                content=(
                    b'{"awsappsyncgraphqlEndpoint":"https://evil.example/graphql",'
                    b'"awsappsyncapiKey":"da2-AAAAAAAAAAAAAAAAAAAAAAAA"}'
                )
            ),
        ]
    )
    crawler._http_request_with_retry = lambda *_args, **_kwargs: next(responses)

    with pytest.raises(RuntimeError, match="unapproved GraphQL endpoint"):
        crawler._resolve_graphql_api_key()


def test_emart_course_collection_prefers_discovered_public_graphql_config() -> None:
    crawler = emart.EmartCrawler.__new__(emart.EmartCrawler)
    crawler._resolve_graphql_api_key = lambda: "da2-" + ("A" * 24)
    crawler.scrape_courses_via_graphql = lambda *_args, **_kwargs: 3
    crawler._navigate = lambda *_args: (_ for _ in ()).throw(AssertionError("browser fallback must not start"))

    assert crawler.scrape_courses("777", "branch-id", max_courses=3) == 3


def test_emart_semester_metadata_is_fetched_once() -> None:
    crawler = emart.EmartCrawler.__new__(emart.EmartCrawler)
    crawler._semester_filters_cache = None
    calls = 0

    def request(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(content=b'{"semester":"S","semesterYear":"2026"}')

    crawler._http_request_with_retry = request

    first = crawler._get_current_semester_filters()
    second = crawler._get_current_semester_filters()

    assert first == second
    assert calls == 1


@pytest.mark.parametrize(
    ("age_group", "label"),
    [
        ("INFANT", "영아"),
        ("TODDLER", "유아"),
        ("CHILD", "아동"),
        ("TEEN", "청소년"),
        ("ADULT", "성인"),
        ("ALL", "전체"),
        (None, ""),
    ],
)
def test_emart_age_group_has_truthful_target_label(age_group: object, label: str) -> None:
    assert emart.target_label_from_age_group(age_group) == label


def test_emart_graphql_saves_one_page_in_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crawler = emart.EmartCrawler.__new__(emart.EmartCrawler)
    crawler.crawl_complete = True
    crawler._active_cursor = None
    page_cursor = object()
    transaction_count = 0
    saved: list[str] = []

    @contextmanager
    def transaction_cursor():
        nonlocal transaction_count
        transaction_count += 1
        yield page_cursor

    monkeypatch.setattr(emart, "get_db_cursor", transaction_cursor)
    crawler._fetch_graphql_courses = lambda *_args: {
        "data": [{"id": "one"}, {"id": "two"}],
        "total": 2,
    }
    crawler._course_data_from_graphql = (
        lambda row, _branch_id, _branch_code: {"provider_course_id": row["id"]}
    )

    def save(course: dict) -> bool:
        assert crawler._active_cursor is page_cursor
        saved.append(course["provider_course_id"])
        return True

    crawler.save_course = save

    assert crawler.scrape_courses_via_graphql("100", "branch-id") == 2
    assert saved == ["one", "two"]
    assert transaction_count == 1
    assert crawler._active_cursor is None


def test_emart_cached_raw_url_identity_avoids_per_row_lookup() -> None:
    crawler = emart.EmartCrawler.__new__(emart.EmartCrawler)
    raw_url = "https://www.cultureclub.emart.com/class/course-1"
    crawler._existing_course_ids_by_raw_url = {raw_url: "legacy-id"}
    crawler._cached_identity_reuse_count = 0
    course = {
        "raw_url": f"{raw_url}?mooncen_course_id=old",
        "provider_course_id": "100:course-1",
    }

    assert crawler._coalesce_course_identity_from_cache(course) is True
    assert course["raw_url"] == raw_url
    assert course["provider_course_id"] == "legacy-id"
    assert crawler._cached_identity_reuse_count == 1

    new_course = {
        "raw_url": "https://www.cultureclub.emart.com/class/course-2",
        "provider_course_id": "100:course-2",
    }
    crawler._remember_course_identity(new_course)
    assert crawler._existing_course_ids_by_raw_url[new_course["raw_url"]] == "100:course-2"


def test_emart_full_run_recycles_browser_after_each_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crawler = emart.EmartCrawler.__new__(emart.EmartCrawler)
    crawler._cached_identity_reuse_count = 0
    crawler.scrape_branches = lambda: [
        {"branch_code": "100", "name": "Store A"},
        {"branch_code": "200", "name": "Store B"},
    ]
    crawler.save_branch = lambda branch: f"{branch['branch_code']}-id"
    crawler._load_existing_course_ids_by_raw_url = lambda: {}
    scraped: list[str] = []
    recycled: list[str] = []
    crawler.scrape_courses = (
        lambda code, *_args, **_kwargs: scraped.append(code) or 1
    )
    crawler._close_driver = lambda: recycled.append("closed")
    monkeypatch.setattr(emart, "mark_stale_courses", lambda *_args: 0)

    assert crawler._run() is True
    assert scraped == ["100", "200"]
    assert recycled == ["closed", "closed"]


def test_municipal_aggregate_gets_extended_default_timeout() -> None:
    assert run_crawlers.effective_provider_timeout_seconds(
        "MUNICIPAL_RESERVATION_TARGETS",
        None,
    ) == 28_800
    assert run_crawlers.effective_provider_timeout_seconds(
        "MUNICIPAL_RESERVATION_TARGETS",
        3_600,
    ) == 3_600


def test_experience_aggregate_gets_extended_default_timeout() -> None:
    assert run_crawlers.effective_provider_timeout_seconds(
        "EXPERIENCE_TARGETS",
        None,
    ) == 28_800
    assert run_crawlers.effective_provider_timeout_seconds(
        "EXPERIENCE_TARGETS",
        3_600,
    ) == 3_600


def test_lotte_mart_gets_extended_default_timeout_without_overriding_operator_choice() -> None:
    assert run_crawlers.effective_provider_timeout_seconds(
        "LOTTE_MART",
        None,
    ) == 28_800
    assert run_crawlers.effective_provider_timeout_seconds(
        "LOTTE_MART",
        3_600,
    ) == 3_600


class _RecoveryCursor:
    def __init__(self, connection: "_RecoveryConnection") -> None:
        self.connection = connection
        self.rows: list[dict[str, object]] = []
        self.rowcount = 1

    def __enter__(self) -> "_RecoveryCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str, params: object = None) -> None:
        self.connection.calls.append((query, params))
        if "RETURNING job.id::text" in query:
            self.rows = [
                {
                    "id": str(uuid4()),
                    "parameters": {"provider": "LOTTE"},
                    "status": "dead_lettered",
                    "retry_count": 0,
                    "available_at": None,
                    "expired_lease_token": str(uuid4()),
                    "expired_lease_epoch": 1,
                }
            ]
        else:
            self.rows = []

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows


class _RecoveryConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.commits = 0

    def cursor(self, **_kwargs: object) -> _RecoveryCursor:
        return _RecoveryCursor(self)

    def commit(self) -> None:
        self.commits += 1


def test_worker_recovers_stale_running_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _RecoveryConnection()
    logs: list[tuple[str, str, str, dict[str, object]]] = []
    monkeypatch.setattr(
        crawler_worker,
        "_append_log",
        lambda _connection, job_id, level, message, metadata=None: logs.append(
            (job_id, level, message, metadata or {})
        ),
    )
    config = crawler_worker.WorkerConfig(
        environment="development",
        agent_id=uuid4(),
        poll_interval=1.0,
        command_timeout=32_400,
    )

    recovered = crawler_worker._recover_stale_jobs(
        connection,
        config,
        stale_after_seconds=300,
    )

    assert recovered == 1
    assert any("leased_until <= CURRENT_TIMESTAMP" in query for query, _params in connection.calls)
    assert any(
        "INSERT INTO ops_crawler_task_observations" in query
        for query, _params in connection.calls
    )
    assert any("UPDATE ops_crawler_runs" in query for query, _params in connection.calls)
    assert logs[0][1] == "error"
    assert logs[0][3]["reason"] == "worker_lease_expired"
