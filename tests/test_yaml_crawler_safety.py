from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
from bs4 import BeautifulSoup
from requests.exceptions import HTTPError, RequestException

from Crawler import Crawler_MunicipalYaml as municipal
from Crawler import Crawler_YamlSources as yaml_sources
from tools import sample_collect_from_yaml as yaml_collectors
from utils import outbound_http


@contextmanager
def _cursor():
    yield object()


def _yaml_rows() -> list[dict[str, str]]:
    return [
        {"branch": "Branch A", "branch_code": "A", "provider_course_id": "one", "title": "Course one", "period": "2026-07-01 ~ 2026-08-01"},
        {"branch": "Branch A", "branch_code": "A", "provider_course_id": "two", "title": "Course two", "period": "2026-07-01 ~ 2026-08-01"},
    ]


class _Connection:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed += 1


def test_yaml_collect_provider_uses_safe_session_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[object] = []

    class FakeSafeSession:
        def __init__(self, **_kwargs) -> None:
            self.headers: dict[str, str] = {}
            self.closed = False
            created.append(self)

        def close(self) -> None:
            self.closed = True

    def collector(_limit: int):
        session = yaml_collectors.session()
        assert session.headers["Accept-Language"].startswith("ko-KR")
        return [], 0, ""

    monkeypatch.setattr(yaml_collectors, "SafeSession", FakeSafeSession)
    monkeypatch.setitem(yaml_collectors.COLLECTORS, "GALLERIA", collector)

    assert yaml_collectors.collect_provider("GALLERIA", 1) == ([], 0, "")
    assert len(created) == 1
    assert created[0].closed is True


def test_yaml_collect_provider_enforces_aggregate_request_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def collector(_limit: int):
        outbound_http._consume_request_budget()  # noqa: SLF001 - verify the shared boundary.
        outbound_http._consume_request_budget()  # noqa: SLF001 - must fail at budget=1.
        return [], 0, ""

    monkeypatch.setitem(yaml_collectors.COLLECTORS, "GALLERIA", collector)

    with pytest.raises(outbound_http.OutboundRequestBlocked, match="budget exhausted"):
        yaml_collectors.collect_provider("GALLERIA", 1, request_budget=1)


def test_yaml_collect_provider_uses_audited_ak_budget_only_for_ak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[int] = []

    @contextmanager
    def fake_sessions(request_budget: int):
        observed.append(request_budget)
        yield

    empty = lambda _limit: ([], 0, "")  # noqa: E731 - compact collector stub.
    monkeypatch.setattr(yaml_collectors, "managed_collector_sessions", fake_sessions)
    monkeypatch.setitem(yaml_collectors.COLLECTORS, "AK_PLAZA", empty)
    monkeypatch.setitem(yaml_collectors.COLLECTORS, "GALLERIA", empty)

    yaml_collectors.collect_provider("AK_PLAZA", 1)
    yaml_collectors.collect_provider("AK_PLAZA", 1, request_budget=17)
    yaml_collectors.collect_provider("GALLERIA", 1)

    assert observed == [
        400,
        17,
        yaml_collectors.PROVIDER_COLLECTOR_REQUEST_BUDGETS["GALLERIA"],
    ]


def test_yaml_collector_request_budget_keeps_a_hard_upper_bound() -> None:
    with pytest.raises(ValueError, match="between 1 and 3000"):
        with yaml_collectors.managed_collector_sessions(3_001):
            pass


def test_lotte_mart_request_budget_covers_bounded_full_snapshot() -> None:
    required_requests = (
        1
        + yaml_collectors.LOTTE_MART_MAX_LIST_PAGES
        + yaml_collectors.LOTTE_MART_DETAIL_LIMIT
    )

    assert (
        required_requests
        <= yaml_collectors.PROVIDER_COLLECTOR_REQUEST_BUDGETS["LOTTE_MART"]
        <= yaml_collectors.MAX_COLLECTOR_REQUEST_BUDGET
    )


def test_eland_request_budget_covers_list_and_detail_caps() -> None:
    required_requests = 2 + yaml_collectors.ELAND_DETAIL_LIMIT

    assert (
        required_requests
        <= yaml_collectors.PROVIDER_COLLECTOR_REQUEST_BUDGETS["ELAND_RETAIL"]
        <= yaml_collectors.MAX_COLLECTOR_REQUEST_BUDGET
    )


def test_eland_full_snapshot_uses_large_list_and_bounded_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, text: str) -> None:
            self.text = text

    class Session:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.page_sizes: list[int] = []
            self.detail_requests = 0

        def get(self, url: str, **_kwargs) -> Response:
            if url.endswith("culture07.do"):
                return Response("<html></html>")
            self.detail_requests += 1
            return Response(
                "<dl><dt>\uac15\uc758\uc2e4</dt><dd>Room A</dd></dl>"
            )

        def post(self, _url: str, *, data: str, **_kwargs) -> Response:
            payload = json.loads(data)
            self.page_sizes.append(payload["PageSize"])
            return Response(
                """
                <table><tbody id="tbodyList">
                  <tr>
                    <td>Store A</td><td>G6</td>
                    <td><a onclick="culture09('A','65','G','1');">
                      Adult class
                    </a></td>
                    <td>Teacher A</td><td>2026.09.01~2026.09.30</td>
                    <td>Monday 09:00~10:00</td><td>10,000</td>
                    <td>Open</td>
                  </tr>
                  <tr>
                    <td>Store B</td><td>C6</td>
                    <td><a onclick="culture09('B','65','C','2');">
                      Child class
                    </a></td>
                    <td>Teacher B</td><td>2026.09.01~2026.09.30</td>
                    <td>Tuesday 10:00~11:00</td><td>20,000</td>
                    <td>Open</td>
                  </tr>
                </tbody></table>
                """
            )

    session = Session()
    monkeypatch.setattr(yaml_collectors, "session", lambda: session)
    monkeypatch.setattr(yaml_collectors, "ELAND_LIST_PAGE_SIZE", 3)
    monkeypatch.setattr(yaml_collectors, "ELAND_DETAIL_LIMIT", 1)

    rows, pages, note = yaml_collectors.eland(100)

    assert len(rows) == 2
    assert pages == 1
    assert session.page_sizes == [3]
    assert session.detail_requests == 1
    assert all(row["period"] == "2026.09.01~2026.09.30" for row in rows)
    assert all(row["schedule_raw"] for row in rows)
    assert all(row["fee"] for row in rows)
    assert all(row["branch"] for row in rows)
    assert all(row["category"] for row in rows)
    assert all(row["target"] for row in rows)
    assert "snapshot_complete=true" in note


def test_lotte_mart_list_card_populates_required_fields() -> None:
    soup = BeautifulSoup(
        """
        <li>
          <div class="lct_view-info">
            <a href="#none" onclick="fn_clsView('20260332210060')">
              <div class="thumb-img">
                <img src="https://example.com/course.jpg" />
              </div>
              <div class="lct_tit-info">
                <p class="showBalloon">Adult fitness</p>
              </div>
              <ul class="lct_sub-info">
                <li>\uc131\uc778\uac15\uc88c</li>
                <li>(\uc6d4) 09:50~10:40</li>
                <li>\uac15\uc88c\uc2dc\uc791\uc77c 2026.09.07</li>
                <li>12\ud68c 90,000\uc6d0</li>
              </ul>
            </a>
          </div>
          <div class="btn_view-area">
            <a class="btn btn-status link">\ubc14\ub85c\uc2e0\uccad</a>
          </div>
        </li>
        """,
        "lxml",
    )
    store = {
        "code": "322",
        "name": "\uc1a1\ud30c\uc810",
        "area_code": "01",
    }

    rows = yaml_collectors.lotte_mart_list_rows(soup, store)

    assert len(rows) == 1
    assert rows[0]["target"] == "\uc131\uc778"
    assert rows[0]["fee"] == "90,000\uc6d0"
    assert rows[0]["period"] == "2026.09.07"
    assert rows[0]["schedule_raw"] == "(\uc6d4) 09:50~10:40"
    assert rows[0]["place"] == "\uc1a1\ud30c\uc810"
    assert rows[0]["category"] == "\uc131\uc778\uac15\uc88c"
    assert rows[0]["sessions"] == 12
    assert rows[0]["image_url"] == "https://example.com/course.jpg"


def test_lotte_mart_full_snapshot_caps_detail_enrichment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Session:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

    stores = [
        {"code": "A", "name": "Store A", "area_code": "01"},
        {"code": "B", "name": "Store B", "area_code": "02"},
    ]
    detail_calls: list[str] = []

    def fetch_page(_session, store, page):
        return (
            [
                {
                    "provider_course_id": f"{store['code']}:{page}",
                    "course_id": f"{store['code']}:{page}",
                    "raw_url": f"https://example.com/{store['code']}/{page}",
                    "title": f"Course {store['code']} {page}",
                    "target": "adult",
                    "period": "2026-09-01",
                    "schedule_raw": "09:00-10:00",
                    "fee": "10000",
                    "category": "fitness",
                    "category_raw": "fitness",
                }
            ],
            (page, 2, 2),
        )

    def detail_fields(_session, raw_url):
        detail_calls.append(raw_url)
        return {"description": "enriched"}

    monkeypatch.setattr(yaml_collectors, "session", Session)
    monkeypatch.setattr(yaml_collectors, "lotte_mart_stores", lambda _session: stores)
    monkeypatch.setattr(yaml_collectors, "lotte_mart_fetch_list_page", fetch_page)
    monkeypatch.setattr(yaml_collectors, "lotte_mart_detail_fields", detail_fields)
    monkeypatch.setattr(yaml_collectors, "LOTTE_MART_DETAIL_LIMIT", 1)

    rows, pages, note = yaml_collectors.lotte_mart(100_000)

    assert len(rows) == 4
    assert pages == 4
    assert len(detail_calls) == 1
    assert sum(row.get("description") == "enriched" for row in rows) == 1
    assert "stores_processed=2" in note
    assert "snapshot_complete=true" in note


def test_sample_field_score_counts_zero_fee_as_present() -> None:
    assert yaml_collectors.score_fields([{"fee": 0}])["fee"] == 1


def test_galleria_exhausts_all_branches_and_populates_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    class Session:
        def __init__(self) -> None:
            self.requests: list[str] = []

        def get(self, url: str, **_kwargs) -> Response:
            self.requests.append(url)
            parsed = urlparse(url)
            if parsed.path.endswith("/open-lecture"):
                if parsed.query:
                    return Response("<html><body></body></html>")
                branch = parsed.path.split("/")[-2]
                return Response(
                    f"""
                    <div class="item">
                      <span class="badge">접수중</span>
                      <div class="item-cont">
                        <a class="item-a"
                           href="/g-culture/culture-center/branch/{branch}/123">
                          <strong class="title">
                            키즈 미술 (A:4-13세) 보호자동반
                          </strong>
                        </a>
                      </div>
                    </div>
                    """
                )
            return Response(
                """
                <dl>
                  <dt>수강료</dt><dd>20,000 원</dd>
                  <dt>강의기간</dt><dd>2026.08.01 ~ 2026.08.01</dd>
                  <dt>강의시간</dt><dd>토 10:00-11:00</dd>
                  <dt>강사</dt><dd>강사</dd>
                  <dt>강의장소</dt><dd>경기도 수원시 예시로 1</dd>
                  <dt>강의실</dt><dd>문화실</dd>
                </dl>
                <section>
                  <h2 class="h">강좌 소개</h2>
                  <p>대상: 4-13세, 보호자동반 미술 수업</p>
                </section>
                <div class="article-pic-mb">
                  <img src="https://example.com/course.jpg" />
                </div>
                """
            )

    session = Session()
    monkeypatch.setattr(yaml_collectors, "session", lambda: session)

    rows, pages, note = yaml_collectors.galleria(100)

    assert len(rows) == 4
    assert pages == 8
    assert note == "snapshot_complete=true branches=4 rows=4 pages=8"
    assert len(session.requests) == 12
    assert all(row["target"] == "4-13세" for row in rows)
    assert all(row["target_with_parent"] is True for row in rows)
    assert all(row["category"] and row["category_raw"] for row in rows)
    assert all(row["address"] == "경기도 수원시 예시로 1" for row in rows)
    assert len({row["provider_course_id"] for row in rows}) == 4


def test_ak_plaza_uses_one_bounded_list_page_per_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, payload: dict | None = None, text: str = "") -> None:
            self._payload = payload or {}
            self.text = text

        def json(self) -> dict:
            return self._payload

    class Session:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.list_sizes: list[int] = []
            self.detail_requests = 0

        def get(self, url: str, **_kwargs) -> Response:
            if "/course/detail" in url:
                self.detail_requests += 1
                return Response(text='<div id="lect_info">강좌 설명</div>')
            return Response(text="<html></html>")

        def post(self, url: str, *, data: dict, **_kwargs) -> Response:
            if url.endswith("/common/change_main_store"):
                return Response({"isSuc": "success"})
            self.list_sizes.append(int(data["listSize"]))
            store = str(data["store"])
            return Response(
                {
                    "pageNum": 1,
                    "list": [
                        {
                            "STORE": store,
                            "SUBJECT_CD": f"{store}-course",
                            "SUBJECT_NM": f"{store} 강좌",
                            "REGIS_FEE": 0,
                            "THUMBNAIL_IMG": f"{store}.jpg",
                        }
                    ],
                }
            )

    session = Session()
    monkeypatch.setattr(yaml_collectors, "session", lambda: session)

    rows, pages, note = yaml_collectors.ak_plaza(100)

    assert len(rows) == len(yaml_collectors.AK_PLAZA_STORES)
    assert pages == len(yaml_collectors.AK_PLAZA_STORES)
    assert session.list_sizes == [1_000] * len(yaml_collectors.AK_PLAZA_STORES)
    assert session.detail_requests == len(rows)
    assert all(row["fee"] == 0 for row in rows)
    assert rows[0]["image_url"].startswith(
        "https://img-culture.akplaza.com/upload/wlect/"
    )
    assert note == "snapshot_complete=true"


def test_ak_single_session_does_not_persist_an_inverted_source_period() -> None:
    assert yaml_collectors.ak_plaza_period(
        {
            "START_YMD": "20261113",
            "END_YMD": "20260907",
            "LECT_CNT": 1,
        }
    ) == "20261113-20261113"


def test_ak_title_age_overrides_generic_api_target() -> None:
    fields = yaml_collectors.target_fields_from_text(
        "[맘앤베이비] 발도장 접시 (2세 미만)"
    )

    assert fields["target"] == "2세 미만"


def test_yaml_source_normalize_preserves_registration_schedule_and_official_application_link() -> None:
    course = yaml_sources.YamlSourceCrawler("GALLERIA").normalize_course(
        {
            "title": "여름 문화 강좌",
            "branch": "명품관",
            "period": "2026-08-01 ~ 2026-08-31",
            "apply_period_raw": "2026-07-15 ~ 2026-07-25",
            "venue_name": "문화홀",
            "venue_address": "서울특별시 예시로 1",
            "raw_url": "https://example.com/course/1",
        },
        "branch-id",
    )

    assert course["apply_start"].isoformat() == "2026-07-15"
    assert course["apply_end"].isoformat() == "2026-07-25"
    assert course["apply_period_raw"] == "2026-07-15 ~ 2026-07-25"
    assert course["application_url"] == "https://example.com/course/1"
    assert course["venue_name"] == "문화홀"
    assert course["venue_address"] == "서울특별시 예시로 1"


@pytest.mark.parametrize(
    "title",
    ["Galleria 로그인", "신규 카드 신청", "영업정보", "개인결제창"],
)
def test_yaml_source_rejects_navigation_rows(title: str) -> None:
    assert yaml_sources.is_non_course_navigation_row(
        {"title": title, "raw_url": "https://dept.galleria.co.kr/g-culture/"}
    )


def test_yaml_source_partial_persistence_fails_and_never_marks_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = "HYUNDAI_DEPT"
    crawler = yaml_sources.YamlSourceCrawler(provider)
    monkeypatch.setitem(yaml_sources.COLLECTORS, provider, lambda _limit: (_yaml_rows(), 1, ""))
    monkeypatch.setattr(crawler, "save_branch", lambda *_args: "branch-id")
    monkeypatch.setattr(
        crawler,
        "normalize_course",
        lambda row, _branch_id: {"provider_course_id": row["provider_course_id"]},
    )
    calls = 0
    transaction_events: list[str] = []

    @contextmanager
    def transaction_cursor():
        try:
            yield object()
        except Exception:
            transaction_events.append("rollback")
            raise
        else:
            transaction_events.append("commit")

    def save_course(_course):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("write failed")
        return True

    monkeypatch.setattr(crawler, "save_course", save_course)
    monkeypatch.setattr(yaml_sources, "get_db_cursor", transaction_cursor)
    stale_calls = []
    monkeypatch.setattr(yaml_sources, "mark_stale_courses", lambda *args: stale_calls.append(args))
    with pytest.raises(RuntimeError, match="failed to persist 1/2"):
        crawler.run(limit=None, mark_stale=True)
    assert stale_calls == []
    assert transaction_events == ["rollback"]


def test_yaml_source_branch_filter_disables_global_stale_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = "HYUNDAI_DEPT"
    crawler = yaml_sources.YamlSourceCrawler(provider)
    monkeypatch.setitem(yaml_sources.COLLECTORS, provider, lambda _limit: (_yaml_rows(), 1, ""))
    monkeypatch.setattr(crawler, "save_branch", lambda *_args: "branch-id")
    monkeypatch.setattr(crawler, "normalize_course", lambda row, _branch_id: row)
    monkeypatch.setattr(crawler, "save_course", lambda _course: True)
    monkeypatch.setattr(yaml_sources, "get_db_cursor", _cursor)
    monkeypatch.setattr(yaml_sources, "delete_empty_branches_for_provider", lambda *_args: None)
    stale_calls = []
    monkeypatch.setattr(yaml_sources, "mark_stale_courses", lambda *args: stale_calls.append(args))
    assert crawler.run(limit=None, mark_stale=True, branch_code="A") == 2
    assert stale_calls == []


def test_yaml_source_enforces_collector_result_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = "HYUNDAI_DEPT"
    crawler = yaml_sources.YamlSourceCrawler(provider)
    rows = [
        {"branch": "A", "branch_code": "A", "provider_course_id": str(index), "title": f"Course {index}", "period": "2026-07-01 ~ 2026-08-01"}
        for index in range(10)
    ]
    monkeypatch.setitem(yaml_sources.COLLECTORS, provider, lambda _limit: (rows, 1, ""))
    monkeypatch.setattr(crawler, "save_branch", lambda *_args: "branch-id")
    monkeypatch.setattr(crawler, "normalize_course", lambda row, _branch_id: row)
    saved = []
    monkeypatch.setattr(crawler, "save_course", lambda course: saved.append(course) or True)
    monkeypatch.setattr(yaml_sources, "get_db_cursor", _cursor)
    monkeypatch.setattr(yaml_sources, "delete_empty_branches_for_provider", lambda *_args: None)
    assert crawler.run(limit=2) == 2
    assert len(saved) == 2


def test_yaml_source_full_run_at_safety_cap_cannot_mark_unseen_rows_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = "HYUNDAI_DEPT"
    crawler = yaml_sources.YamlSourceCrawler(provider)
    monkeypatch.setattr(yaml_sources, "UNLIMITED_COLLECTOR_LIMIT", 2)
    monkeypatch.setitem(yaml_sources.COLLECTORS, provider, lambda _limit: (_yaml_rows(), 1, ""))
    monkeypatch.setattr(crawler, "save_branch", lambda *_args: "branch-id")
    monkeypatch.setattr(crawler, "normalize_course", lambda row, _branch_id: row)
    monkeypatch.setattr(crawler, "save_course", lambda _course: True)
    monkeypatch.setattr(yaml_sources, "get_db_cursor", _cursor)
    monkeypatch.setattr(yaml_sources, "delete_empty_branches_for_provider", lambda *_args: None)
    stale_calls = []
    monkeypatch.setattr(yaml_sources, "mark_stale_courses", lambda *args: stale_calls.append(args))

    assert crawler.run(limit=None, mark_stale=True) == 2
    assert stale_calls == []


@pytest.mark.parametrize(
    ("note", "expected_stale_calls"),
    [
        ("snapshot_complete=true", 1),
        ("snapshot_complete=false", 0),
        ("", 0),
    ],
)
def test_yaml_source_stale_cleanup_requires_complete_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    note: str,
    expected_stale_calls: int,
) -> None:
    provider = "HYUNDAI_DEPT"
    crawler = yaml_sources.YamlSourceCrawler(provider)
    monkeypatch.setitem(
        yaml_sources.COLLECTORS,
        provider,
        lambda _limit: (_yaml_rows(), 1, note),
    )
    monkeypatch.setattr(crawler, "save_branch", lambda *_args: "branch-id")
    monkeypatch.setattr(crawler, "normalize_course", lambda row, _branch_id: row)
    monkeypatch.setattr(crawler, "save_course", lambda _course: True)
    monkeypatch.setattr(yaml_sources, "get_db_cursor", _cursor)
    monkeypatch.setattr(yaml_sources, "delete_empty_branches_for_provider", lambda *_args: None)
    stale_calls = []
    monkeypatch.setattr(
        yaml_sources,
        "mark_stale_courses",
        lambda *args: stale_calls.append(args) or 0,
    )

    assert crawler.run(limit=None, mark_stale=True) == 2
    assert len(stale_calls) == expected_stale_calls


def test_yaml_source_save_helpers_reuse_active_transaction_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crawler = yaml_sources.YamlSourceCrawler("HYUNDAI_DEPT")

    class Cursor:
        def __init__(self) -> None:
            self.executed = 0

        def execute(self, *_args, **_kwargs) -> None:
            self.executed += 1

        def fetchone(self):
            return {"id": "branch-id"}

    cursor = Cursor()
    crawler._active_cursor = cursor
    monkeypatch.setattr(
        yaml_sources,
        "get_db_cursor",
        lambda: pytest.fail("save helper opened a nested transaction"),
    )
    monkeypatch.setattr(yaml_sources, "should_skip_expired_course", lambda _course: False)
    monkeypatch.setattr(yaml_sources, "coalesce_provider_course_id_by_raw_url", lambda *_args: None)

    assert crawler.save_branch("branch", "Branch") == "branch-id"
    assert crawler.save_course(
        {"provider": "HYUNDAI_DEPT", "provider_course_id": "course-1", "title": "Course"}
    ) is True
    assert cursor.executed == 2


def _target(provider: str, url: str) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=provider,
        name=provider,
        branch=provider,
        url=url,
        source="test",
    )


def test_municipal_stale_cleanup_is_endpoint_scoped_after_every_target_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = "MUNI_TEST"
    targets = [
        _target(provider, "https://example.test/a?access_token=secret-a"),
        _target(provider, "https://example.test/b?token=secret-b"),
    ]
    events = []
    monkeypatch.setattr(municipal, "utc_now", lambda: events.append("cutoff") or "crawl-cutoff")
    monkeypatch.setattr(municipal, "load_targets", lambda *_args, **_kwargs: targets)
    monkeypatch.setattr(
        municipal,
        "collect_from_url",
        lambda target, **_kwargs: (
            events.append(f"collect:{target.url}")
            or ([{"title": target.name, "raw_url": target.url}], "test", {"pages": 1})
        ),
    )

    class Writer:
        def __init__(self, _provider):
            pass

        def save_rows(self, rows):
            return len(rows)

    monkeypatch.setattr(municipal, "MunicipalDbWriter", Writer)
    connection = _Connection()
    monkeypatch.setattr(municipal, "get_db_connection", lambda: connection)
    stale_calls = []
    monkeypatch.setattr(
        municipal,
        "mark_stale_courses",
        lambda target_provider, cutoff, *, source_endpoint: stale_calls.append(
            (target_provider, cutoff, source_endpoint)
        )
        or 0,
    )
    reports = municipal.run(
        source="municipal",
        target_limit=None,
        per_target_limit=0,
        min_score=0,
        include_review=False,
        save_db=True,
        mark_stale=True,
        max_depth=0,
        max_pages=1,
        detail_limit=0,
        timeout=1,
    )
    assert stale_calls == [
        (provider, "crawl-cutoff", "https://example.test/a"),
        (provider, "crawl-cutoff", "https://example.test/b"),
    ]
    assert connection.commits == 1 and connection.rollbacks == 0 and connection.closed == 1
    assert events[0] == "cutoff"
    assert all(report.success for report in reports)
    assert all("secret" not in report.url for report in reports)


def test_municipal_failed_sibling_target_blocks_provider_stale_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = "MUNI_TEST"
    targets = [_target(provider, "https://example.test/a"), _target(provider, "https://example.test/b")]
    monkeypatch.setattr(municipal, "load_targets", lambda *_args, **_kwargs: targets)
    calls = 0

    def collect(target, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("collector failed")
        return [{"title": target.name, "raw_url": target.url}], "test", {"pages": 1}

    monkeypatch.setattr(municipal, "collect_from_url", collect)

    class Writer:
        def __init__(self, _provider):
            pass

        def save_rows(self, rows):
            return len(rows)

    monkeypatch.setattr(municipal, "MunicipalDbWriter", Writer)
    monkeypatch.setattr(municipal, "get_db_connection", lambda: pytest.fail("failed sibling opened the database"))
    stale_calls = []
    monkeypatch.setattr(municipal, "mark_stale_courses", lambda *args: stale_calls.append(args))
    reports = municipal.run(
        source="municipal",
        target_limit=None,
        per_target_limit=0,
        min_score=0,
        include_review=False,
        save_db=True,
        mark_stale=True,
        max_depth=0,
        max_pages=1,
        detail_limit=0,
        timeout=1,
    )
    assert stale_calls == []
    assert [report.success for report in reports] == [True, False]


def test_municipal_main_requires_all_targets_to_succeed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    reports = [
        municipal.ProviderReport("GOOD", "good", "https://example.test/good", success=True),
        municipal.ProviderReport("BAD", "bad", "https://example.test/bad", success=False),
    ]
    monkeypatch.setattr(municipal, "run", lambda **_kwargs: reports)
    monkeypatch.setattr(municipal, "print_table", lambda _reports: None)
    monkeypatch.setattr(municipal, "write_report", lambda _reports: tmp_path / "report.yaml")
    assert municipal.main(["--source", "municipal"]) == 1
    with pytest.raises(SystemExit):
        municipal.main(["--source", "municipal", "--mark-stale"])
    with pytest.raises(SystemExit):
        municipal.main(["--source", "municipal", "--save-db"])


def test_municipal_report_names_are_unique_within_one_second(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(municipal, "REPORT_DIR", tmp_path)
    first = municipal.write_report([])
    second = municipal.write_report([])
    assert first != second
    assert first.is_file()
    assert second.is_file()
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize(
    "path",
    [
        "/site/edtotal/lesson/userlist.do",
        "/site/edtotal/lifeStudy/userlist.do",
        "/site/edtotal/eachOther/userlist.do",
        "/site/edtotal/happyStudy/userlist.do",
    ],
)
def test_yongsan_legacy_https_dispatch_preserves_each_input_path(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    url = f"https://yedu.yongsan.go.kr{path}?sitecdv=S0000500&menucdv=02000000"
    target = _target("MUNI_YEDU_YONGSAN_GO_KR_36A48D5E", url)
    requested_urls: list[str] = []
    sessions = []

    class TrackedSession:
        def __init__(self, **_kwargs) -> None:
            self.headers = {}
            self.closed = False
            sessions.append(self)

        def close(self) -> None:
            self.closed = True

    def collect_lesson(actual_target, **_kwargs):
        requested_urls.append(actual_target.url)
        return [], "yongsan_lessons", {"pages": 1, "detail_pages": 0}

    def fetch(_session, actual_url, **_kwargs):
        requested_urls.append(actual_url)
        return BeautifulSoup("<html><body></body></html>", "html.parser")

    monkeypatch.setattr(municipal, "SafeSession", TrackedSession)
    monkeypatch.setattr(municipal, "collect_yongsan_lessons", collect_lesson)
    monkeypatch.setattr(municipal, "fetch_soup", fetch)
    monkeypatch.setattr(municipal, "parse_all_courses", lambda *_args: ([], "none"))
    monkeypatch.setattr(municipal, "discover_links", lambda *_args: ([], False))
    monkeypatch.setattr(municipal, "discover_reservation_links", lambda *_args: [])

    municipal.collect_from_url(
        target,
        timeout=10,
        max_depth=1,
        max_pages=1,
        detail_limit=1,
    )

    assert requested_urls == [url]
    assert all(not session or session.closed for session in sessions)


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("timeout", 0),
        ("timeout", 121),
        ("max_depth", -1),
        ("max_depth", 6),
        ("max_pages", 0),
        ("max_pages", 2_001),
        ("detail_limit", -1),
        ("detail_limit", 3_001),
    ],
)
def test_collect_from_url_rejects_zero_negative_and_upper_bound_plus_one(
    keyword: str,
    value: int,
) -> None:
    with pytest.raises(ValueError, match=keyword):
        municipal.collect_from_url(
            _target("MUNI_TEST", "https://example.test/courses"),
            **{keyword: value},
        )


def test_generic_collector_enforces_page_budget_and_closes_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []
    sessions = []

    class TrackedSession:
        def __init__(self, **_kwargs) -> None:
            self.headers = {}
            self.closed = False
            sessions.append(self)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(municipal, "SafeSession", TrackedSession)
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda _session, url, **_kwargs: (
            requested_urls.append(url)
            or BeautifulSoup("<html><body></body></html>", "html.parser")
        ),
    )
    monkeypatch.setattr(municipal, "parse_all_courses", lambda *_args: ([], "none"))
    monkeypatch.setattr(
        municipal,
        "discover_links",
        lambda *_args: ([f"https://example.test/courses?page={index}" for index in range(10)], True),
    )
    monkeypatch.setattr(municipal, "discover_reservation_links", lambda *_args: [])

    _rows, _parser, meta = municipal.collect_from_url(
        _target("MUNI_TEST", "https://example.test/courses"),
        timeout=10,
        max_depth=1,
        max_pages=2,
        detail_limit=1,
    )

    assert len(requested_urls) == 2
    assert meta["pages"] == 2
    assert len(sessions) == 1
    assert sessions[0].closed is True


def test_generic_collector_does_not_refetch_synthetic_fragment_as_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    list_url = "https://example.test/reservation/program/list"
    raw_url = municipal.stable_list_item_url(
        list_url,
        "MUNI_TEST",
        "가족 과학 교실",
        "과학관",
        "2026-08-01 ~ 2026-08-31",
        "토 10:00",
    )
    requested_urls: list[str] = []
    row = {
        "provider": "MUNI_TEST",
        "provider_course_id": "course-1",
        "title": "가족 과학 교실",
        "branch": "과학관",
        "raw_url": raw_url,
        "application_url": list_url,
        "period": "2026-08-01 ~ 2026-08-31",
        "schedule_raw": "토 10:00",
        "status": "OPEN",
        "raw_fields": {"parser": "generic_table", "source_url": list_url},
    }

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda _session, url, **_kwargs: (
            requested_urls.append(url)
            or BeautifulSoup("<html><body></body></html>", "html.parser")
        ),
    )
    monkeypatch.setattr(municipal, "parse_all_courses", lambda *_args: ([dict(row)], "generic_table"))
    monkeypatch.setattr(municipal, "discover_links", lambda *_args: ([], False))
    monkeypatch.setattr(municipal, "discover_reservation_links", lambda *_args: [])

    _rows, _parser, meta = municipal.collect_from_url(
        _target("MUNI_TEST", list_url),
        timeout=10,
        max_depth=1,
        max_pages=1,
        detail_limit=3,
    )

    assert requested_urls == [list_url]
    assert meta["detail_pages"] == 0


def test_municipal_fetch_retry_never_exceeds_requested_timeout() -> None:
    timeouts: list[int] = []

    class FailingSession:
        def get(self, _url: str, **kwargs):
            timeouts.append(kwargs["timeout"])
            raise RequestException("offline")

    with pytest.raises(RequestException, match="Strict TLS request failed"):
        municipal.fetch_soup(FailingSession(), "https://example.test/courses", timeout=10)
    assert timeouts == [10, 10]


@pytest.mark.parametrize(
    "hostname",
    ["reserve.busan.go.kr", "www.gongju.go.kr"],
)
def test_transient_tls_hosts_retry_is_paced_and_bounded(
    monkeypatch,
    hostname: str,
) -> None:
    timeouts: list[int] = []
    sleeps: list[float] = []

    class Response:
        content = b"<html><body>ok</body></html>"
        encoding = "utf-8"
        apparent_encoding = "utf-8"
        text = "<html><body>ok</body></html>"

        def raise_for_status(self) -> None:
            return None

    class RecoveringSession:
        def get(self, _url: str, **kwargs):
            timeouts.append(kwargs["timeout"])
            if len(timeouts) < 3:
                raise RequestException("temporary TLS failure")
            return Response()

    monkeypatch.setattr(municipal.time, "sleep", sleeps.append)
    soup = municipal.fetch_soup(
        RecoveringSession(),
        f"https://{hostname}/courses",
        timeout=10,
    )

    assert soup.get_text(" ", strip=True) == "ok"
    assert timeouts == [10, 10, 10]
    assert sleeps == [0.25, 0.5]


@pytest.mark.parametrize("hostname", ["www.naju.go.kr", "www.wando.go.kr"])
def test_cold_bad_request_hosts_retry_http_400(
    monkeypatch,
    hostname: str,
) -> None:
    statuses = [400, 200]
    sleeps: list[float] = []
    closed: list[int] = []

    class Response:
        content = b"<html><body>ok</body></html>"
        encoding = "utf-8"
        apparent_encoding = "utf-8"
        text = "<html><body>ok</body></html>"

        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

        def close(self) -> None:
            closed.append(self.status_code)

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise HTTPError(str(self.status_code))

    class RecoveringSession:
        def get(self, _url: str, **_kwargs):
            return Response(statuses.pop(0))

    monkeypatch.setattr(municipal.time, "sleep", sleeps.append)
    soup = municipal.fetch_soup(
        RecoveringSession(),
        f"https://{hostname}/course/list",
        timeout=10,
    )

    assert soup.get_text(" ", strip=True) == "ok"
    assert statuses == []
    assert closed == [400]
    assert sleeps == [0.25]


def test_unscoped_http_400_is_not_retried() -> None:
    calls = 0

    class Response:
        status_code = 400
        content = b"bad request"
        encoding = "utf-8"
        apparent_encoding = "utf-8"
        text = "bad request"

        def raise_for_status(self) -> None:
            raise HTTPError("400")

    class FailingSession:
        def get(self, _url: str, **_kwargs):
            nonlocal calls
            calls += 1
            return Response()

    with pytest.raises(HTTPError, match="400"):
        municipal.fetch_soup(
            FailingSession(),
            "https://example.test/course/list",
            timeout=10,
        )
    assert calls == 1


def test_crawler_python_sources_contain_no_plaintext_http_endpoints() -> None:
    crawler_root = Path(__file__).resolve().parents[1] / "Crawler"
    offenders = [
        str(path.relative_to(crawler_root))
        for path in crawler_root.rglob("*.py")
        if "http://" in path.read_text(encoding="utf-8-sig")
    ]
    assert offenders == []
