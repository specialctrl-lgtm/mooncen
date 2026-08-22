from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import importlib

import pytest
from bs4 import BeautifulSoup

import Crawler.generated_yaml.manual_generic_crawler as crawler
from utils.outbound_http import SafeSession


def sample_row(provider: str = "TEST_PROVIDER") -> dict:
    return {
        "provider": provider,
        "provider_course_id": "course-1",
        "title": "Course",
        "branch": "Branch",
        "branch_code": "branch-1",
        "address": "",
        "raw_url": "https://example.com/course/1",
        "source_endpoint": "https://example.com/courses?category=education",
        "application_url": "https://example.com/course/1",
        "application_type": "ONLINE_RESERVATION",
        "reservation_available": True,
        "period": "2026.08.01 ~ 2026.08.31",
        "start_date": "2026-08-01",
        "end_date": "2026-08-31",
        "schedule_raw": "월 10:00 ~ 11:00",
        "target": "성인",
        "fee_raw": "무료",
        "fee": 0,
        "status": "OPEN",
        "status_raw": "접수중",
        "description": "Description",
        "image_url": "",
        "venue_name": "Branch",
        "venue_address": "",
        "collection_category": "교육",
        "domain_category": "평생학습",
        "source_group": "manual_target",
        "operator_type": "public",
        "collection_type": "manual_generic",
        "program_type": "강좌",
        "raw_fields": {
            "source_endpoint": "https://example.com/courses?category=education",
        },
    }


def test_manual_generic_transport_uses_safe_session() -> None:
    session = crawler.session()
    try:
        assert isinstance(session, SafeSession)
        assert session.trust_env is False
        assert session.verify is True
    finally:
        session.close()


@pytest.mark.parametrize(
    "module_name",
    [
        "Crawler.Crawler_AnyangLearning",
        "Crawler.Crawler_BabsangWelfare",
        "Crawler.Crawler_YonginLifelong",
    ],
)
def test_generic_manual_wrappers_import_recovered_common_engine(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert module.run_cli is crawler.run_cli


def test_fetch_soup_never_disables_tls_verification() -> None:
    calls: list[dict] = []

    class Response:
        encoding = "utf-8"
        apparent_encoding = "utf-8"
        text = "<html><title>ok</title></html>"
        url = "https://example.com/courses"
        history: list = []

        def raise_for_status(self) -> None:
            return None

        def close(self) -> None:
            return None

    class Session:
        def get(self, url: str, **kwargs):
            calls.append({"url": url, **kwargs})
            return Response()

    soup = crawler.fetch_soup(Session(), "https://example.com/courses", 5)
    assert soup.title.get_text() == "ok"
    assert calls == [{"url": "https://example.com/courses", "timeout": 5, "verify": True}]


def test_fetch_soup_rejects_https_to_http_redirect() -> None:
    class Response:
        encoding = "utf-8"
        apparent_encoding = "utf-8"
        text = ""
        url = "http://example.com/courses"
        history: list = []

        def close(self) -> None:
            return None

    class Session:
        def get(self, url: str, **kwargs):
            return Response()

    with pytest.raises(Exception, match="plaintext redirect"):
        crawler.fetch_soup(Session(), "https://example.com/courses", 5)


def test_short_korean_dates_and_hour_only_schedules_are_normalized() -> None:
    text = "운영기간 26.4.13 ~ 26.6.29 / 월요일 / 15~17시"

    assert crawler.parse_date_range(text) == ("2026-04-13", "2026-06-29")
    assert crawler.normalize_schedule(text) == "월요일 / 15:00~17:00"
    assert crawler.normalize_schedule("주소 81-2") == ""
    assert crawler.normalize_schedule("강좌 기간 2~8월") == ""
    assert crawler.normalize_schedule("대상 4-13세") == ""
    inferred = crawler.infer_from_text(text)
    assert inferred["schedule_raw"] == "월요일 / 15:00~17:00"


def test_detail_enrichment_keeps_truthful_unknowns_and_specific_venue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soup = BeautifulSoup(
        """
        <html><body>
          <dl><dt>운영기간</dt><dd>26.5.18 ~ 26.9.30</dd></dl>
          <dl><dt>장소</dt><dd>복지관 1층 공유주방</dd></dl>
          <dl><dt>모집상태</dt><dd>접수중</dd></dl>
        </body></html>
        """,
        "html.parser",
    )
    monkeypatch.setattr(crawler, "fetch_soup", lambda *_args, **_kwargs: soup)
    row = {
        "raw_url": "https://example.com/program/1",
        "venue_name": "복지관",
    }

    crawler.enrich_detail(object(), row, 5)

    assert row["start_date"] == "2026-05-18"
    assert row["end_date"] == "2026-09-30"
    assert row["venue_name"] == "복지관 1층 공유주방"
    assert row["target"] == "연령 미정"
    assert row["fee_raw"] == "요금 별도 안내"
    assert row["fee"] is None
    assert row["fee_status"] == "UNKNOWN"


@pytest.mark.parametrize(
    "href",
    [
        "/news/articleView.do?articleSeq=77&category=education",
        "/board/view.do?id=77",
        "/press/releases/77",
    ],
)
def test_notice_article_and_press_board_rows_are_not_candidates(href: str) -> None:
    soup = BeautifulSoup(
        f"""
        <html><head><title>공지사항</title></head><body>
          <h1>공지사항</h1>
          <ul class="board-list"><li>
            <a href="{href}">2026 여름 강좌 참여자 모집</a>
            <span>모집기간 2026.08.01 ~ 2026.08.20 대상 성인 교육시간 10:00</span>
          </li></ul>
        </body></html>
        """,
        "html.parser",
    )

    assert crawler.candidate_items(soup, "https://example.go.kr/notice/list") == []


def test_empty_first_page_title_and_body_are_not_published_as_online_course(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

    monkeypatch.setattr(
        crawler,
        "provider_meta",
        lambda provider: {
            "provider": provider,
            "name": "Test",
            "url": "https://example.go.kr/start",
            "crawler_status": "ready",
        },
    )
    monkeypatch.setattr(crawler, "session", FakeSession)
    monkeypatch.setattr(
        crawler,
        "fetch_soup",
        lambda *_args, **_kwargs: BeautifulSoup(
            """
            <html><head><title>교육 참여자 모집 안내</title></head>
            <body>모집기간 2026.08.01 ~ 2026.08.20 대상 성인</body></html>
            """,
            "html.parser",
        ),
    )

    rows, meta = crawler.collect(
        "TEST_PROVIDER",
        limit=0,
        max_pages=1,
        detail_limit=0,
        timeout=5,
        max_depth=0,
    )

    assert rows == []
    assert meta["pages"] == 1
    assert meta["menu_fallback_links"] == 0
    assert meta["detail_pages"] == 0
    assert meta["eligibility_complete"] is False
    assert meta["complete"] is False


def test_empty_configured_page_uses_only_ranked_safe_same_site_menu_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_url = "https://example.go.kr/start"
    course_list_url = "https://example.go.kr/education/program/list"
    calls: list[str] = []
    pages = {
        root_url: BeautifulSoup(
            """
            <html><head><title>복지관</title></head><body><nav>
              <a href="/news/articleView.do?articleSeq=1">강좌 모집 공지</a>
              <a href="/member/login">수강신청 로그인</a>
              <a href="/download/course.hwp">강좌 다운로드</a>
              <a href="https://evil.example/program/list">교육신청</a>
              <a href="/education/program/list">수강신청</a>
            </nav></body></html>
            """,
            "html.parser",
        ),
        course_list_url: BeautifulSoup(
            """
            <html><head><title>수강신청</title></head><body>
              <ul class="program-list"><li>
                <a href="/education/program/detail?id=10">성인 요리 강좌</a>
                <span>교육기간 2026.08.01 ~ 2026.08.31 월요일 10:00~11:00
                대상 성인 수강료 무료 신청하기</span>
              </li></ul>
            </body></html>
            """,
            "html.parser",
        ),
    }

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

    def fake_fetch(_session, url: str, _timeout: int):
        calls.append(url)
        return pages[url]

    monkeypatch.setattr(
        crawler,
        "provider_meta",
        lambda provider: {
            "provider": provider,
            "name": "Test",
            "url": root_url,
            "crawler_status": "ready",
        },
    )
    monkeypatch.setattr(crawler, "session", FakeSession)
    monkeypatch.setattr(crawler, "fetch_soup", fake_fetch)

    rows, meta = crawler.collect(
        "TEST_PROVIDER",
        limit=0,
        max_pages=2,
        detail_limit=0,
        timeout=5,
        max_depth=0,
    )

    assert [row["title"] for row in rows] == ["성인 요리 강좌"]
    assert calls == [root_url, course_list_url]
    assert meta["menu_fallback_links"] == 1
    assert meta["menu_fallback_pages"] == 1
    assert meta["eligibility_complete"] is False
    assert meta["complete"] is False


def test_menu_fallback_follows_same_site_course_iframe_and_safe_get_form() -> None:
    soup = BeautifulSoup(
        """
        <html><body>
          <iframe src="/education/program/list.do" title="교육 프로그램"></iframe>
          <form method="get" action="/lecture/search.do" aria-label="강좌 검색">
            <input type="hidden" name="category" value="adult">
            <select name="status"><option value="open" selected>접수중</option></select>
          </form>
          <iframe src="https://evil.example/course/list" title="교육 프로그램"></iframe>
          <form method="post" action="/course/apply.do" aria-label="수강신청">
            <input type="email" name="email">
          </form>
        </body></html>
        """,
        "html.parser",
    )

    links = crawler.ranked_menu_fallback_links(soup, "https://example.go.kr/start")

    assert "https://example.go.kr/education/program/list.do" in links
    assert "https://example.go.kr/lecture/search.do?category=adult&status=open" in links
    assert all("evil.example" not in link for link in links)
    assert all("apply.do" not in link for link in links)


def test_babsang_structured_program_row_remains_publishable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://www.bsbokji.or.kr/participation/program/"
    soup = BeautifulSoup(
        """
        <html><head><title>프로그램 신청</title></head><body>
          <ul class="program-list"><li>
            <a href="/participation/program/?mode=view&amp;idx=14">건강 요리 프로그램</a>
            <span>교육기간 2026.08.01 ~ 2026.08.31 화요일 14:00~16:00
            대상 성인 참가비 10,000원 프로그램신청</span>
          </li></ul>
        </body></html>
        """,
        "html.parser",
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

    monkeypatch.setattr(
        crawler,
        "provider_meta",
        lambda provider: {
            "provider": provider,
            "name": "밥상공동체종합사회복지관",
            "url": url,
            "crawler_status": "ready",
            "collection_category": "복지관",
        },
    )
    monkeypatch.setattr(crawler, "session", FakeSession)
    monkeypatch.setattr(crawler, "fetch_soup", lambda *_args, **_kwargs: soup)

    rows, meta = crawler.collect(
        "BABSANG_WELFARE_PROGRAM",
        limit=1,
        max_pages=1,
        detail_limit=0,
        timeout=5,
        max_depth=0,
    )

    assert [row["title"] for row in rows] == ["건강 요리 프로그램"]
    assert rows[0]["fee"] == 10000
    assert rows[0]["raw_fields"]["eligibility_reason"] in {
        "structured_course_fields",
        "scoped_course_fields",
    }
    assert meta["eligibility_complete"] is True


def test_save_db_refuses_ineligible_editorial_row_before_opening_database() -> None:
    row = sample_row()
    row["raw_url"] = "https://example.go.kr/news/articleView.do?articleSeq=77"
    row["application_url"] = row["raw_url"]

    with pytest.raises(ValueError, match="editorial_article_url"):
        crawler.save_db([row])


@pytest.mark.parametrize(
    "arguments",
    [
        ["--limit", "5001"],
        ["--max-pages", "121"],
        ["--detail-limit", "1201"],
        ["--timeout", "61"],
        ["--max-depth", "2"],
        ["--max-depth", "4"],
    ],
)
def test_manual_generic_cli_bounds(arguments: list[str]) -> None:
    with pytest.raises(SystemExit):
        crawler.parse_args("TEST_PROVIDER", arguments)


def test_manual_generic_cli_stale_and_persistence_boundaries() -> None:
    with pytest.raises(SystemExit):
        crawler.parse_args("TEST_PROVIDER", ["--save-db", "--dry-run"])
    with pytest.raises(SystemExit):
        crawler.parse_args("TEST_PROVIDER", ["--mark-stale"])
    with pytest.raises(SystemExit):
        crawler.parse_args("TEST_PROVIDER", ["--save-db"])
    with pytest.raises(SystemExit):
        crawler.parse_args("TEST_PROVIDER", ["--save-db", "--limit", "0", "--allow-partial-save"])
    sampled = crawler.parse_args("TEST_PROVIDER", ["--save-db", "--allow-partial-save"])
    assert sampled.save_db and sampled.allow_partial_save and sampled.limit == 10
    with pytest.raises(SystemExit):
        crawler.parse_args("TEST_PROVIDER", ["--save-db", "--mark-stale"])
    args = crawler.parse_args("TEST_PROVIDER", ["--save-db", "--mark-stale", "--limit", "0"])
    assert args.save_db and args.mark_stale and args.limit == 0


def test_collect_closes_session_and_surfaces_detail_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"closed": False}

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            state["closed"] = True

    monkeypatch.setattr(
        crawler,
        "provider_meta",
        lambda provider: {
            "provider": provider,
            "name": "Test",
            "url": "https://example.com",
            "crawler_status": "ready",
        },
    )
    monkeypatch.setattr(crawler, "session", FakeSession)
    monkeypatch.setattr(crawler, "fetch_soup", lambda *args, **kwargs: BeautifulSoup("<html></html>", "html.parser"))
    monkeypatch.setattr(
        crawler,
        "candidate_items",
        lambda soup, url: [
            {
                "title": "성인 요리 강좌",
                "raw_url": "https://example.com/course/1",
                "container_text": ("교육기간 2026.08.01 ~ 2026.08.31 월요일 10:00~11:00 대상 성인 수강료 무료"),
            }
        ],
    )
    monkeypatch.setattr(
        crawler, "enrich_detail", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("token=secret"))
    )
    rows, meta = crawler.collect("TEST_PROVIDER", limit=1, max_pages=1, detail_limit=1, timeout=5)
    assert len(rows) == 1
    assert state["closed"] is True
    assert meta["complete"] is False
    assert len(meta["errors"]) == 1
    assert "secret" not in meta["errors"][0]


def test_max_depth_zero_explicitly_disables_detail_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

    monkeypatch.setattr(
        crawler,
        "provider_meta",
        lambda provider: {
            "provider": provider,
            "name": "Test",
            "url": "https://example.com",
            "crawler_status": "ready",
        },
    )
    monkeypatch.setattr(crawler, "session", FakeSession)
    monkeypatch.setattr(crawler, "fetch_soup", lambda *args, **kwargs: BeautifulSoup("<html></html>", "html.parser"))
    monkeypatch.setattr(
        crawler,
        "candidate_items",
        lambda soup, url: [
            {
                "title": "성인 요리 강좌",
                "raw_url": "https://example.com/course/1",
                "container_text": ("교육기간 2026.08.01 ~ 2026.08.31 월요일 10:00~11:00 대상 성인 수강료 무료"),
            }
        ],
    )
    monkeypatch.setattr(
        crawler, "enrich_detail", lambda *args, **kwargs: pytest.fail("detail request was not disabled")
    )
    rows, meta = crawler.collect("TEST_PROVIDER", limit=1, max_pages=1, detail_limit=10, timeout=5, max_depth=0)
    assert len(rows) == 1
    assert meta["detail_pages"] == 0
    assert meta["detail_cap_reached"] is True


def test_explicit_dry_run_never_calls_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        crawler,
        "collect",
        lambda *args, **kwargs: (
            [sample_row()],
            {"parser": "fixture", "pages": 1, "detail_pages": 1, "complete": True, "errors": []},
        ),
    )
    monkeypatch.setattr(crawler, "save_db", lambda *args, **kwargs: pytest.fail("dry-run wrote to the database"))
    assert crawler.run_cli("TEST_PROVIDER", ["--dry-run"]) == 0


def test_partial_collection_failure_exits_nonzero_without_database_write(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        crawler,
        "collect",
        lambda *args, **kwargs: (
            [sample_row()],
            {"parser": "fixture", "pages": 1, "detail_pages": 1, "complete": False, "errors": ["detail failed"]},
        ),
    )
    monkeypatch.setattr(crawler, "save_db", lambda *args, **kwargs: pytest.fail("partial rows were saved"))
    assert crawler.run_cli("TEST_PROVIDER", ["--save-db", "--allow-partial-save"]) == 1


def test_mark_stale_requires_proven_complete_uncapped_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        crawler,
        "collect",
        lambda *args, **kwargs: (
            [sample_row()],
            {"parser": "fixture", "pages": 2, "detail_pages": 1, "complete": False, "errors": []},
        ),
    )
    monkeypatch.setattr(crawler, "save_db", lambda *args, **kwargs: pytest.fail("incomplete stale crawl was saved"))
    assert crawler.run_cli("TEST_PROVIDER", ["--save-db", "--mark-stale", "--limit", "0"]) == 1


def test_mark_stale_passes_pre_crawl_cutoff_into_atomic_save(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        crawler,
        "collect",
        lambda *args, **kwargs: (
            [sample_row()],
            {"parser": "fixture", "pages": 2, "detail_pages": 1, "complete": True, "errors": []},
        ),
    )

    def fake_save(rows, **kwargs):
        captured.update(kwargs)
        return len(rows)

    monkeypatch.setattr(crawler, "save_db", fake_save)
    assert crawler.run_cli("TEST_PROVIDER", ["--save-db", "--mark-stale", "--limit", "0"]) == 0
    assert captured["stale_provider"] == "TEST_PROVIDER"
    assert isinstance(captured["stale_cutoff"], datetime)


def test_save_and_stale_sql_share_one_cursor_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[tuple[str, object]] = []
    contexts = 0

    class Cursor:
        def __init__(self) -> None:
            self.last_query = ""

        def execute(self, query: str, params: object = None) -> None:
            self.last_query = " ".join(query.split())
            executed.append((self.last_query, params))

        def fetchone(self) -> dict[str, str] | None:
            if self.last_query.startswith("SELECT provider_course_id"):
                return None
            return {"id": "branch-id"}

    @contextmanager
    def fake_cursor():
        nonlocal contexts
        contexts += 1
        yield Cursor()

    import DB.db_utils

    monkeypatch.setattr(DB.db_utils, "get_db_cursor", fake_cursor)
    cutoff = datetime.now().astimezone()
    source_endpoint = "https://example.com/courses?category=education"
    assert crawler.save_db(
        [sample_row()],
        stale_provider="TEST_PROVIDER",
        stale_cutoff=cutoff,
        stale_source_endpoint=source_endpoint,
    ) == 1
    assert contexts == 1
    assert "INSERT INTO branches" in executed[0][0]
    assert executed[1][0].startswith("SELECT provider_course_id")
    assert "INSERT INTO courses" in executed[2][0]
    assert "removed_at = NULL" in executed[2][0]
    assert "UPDATE courses SET is_active = FALSE" in executed[3][0]
    assert "source_endpoint = %s" in executed[3][0]
    assert executed[3][1] == ("TEST_PROVIDER", source_endpoint, cutoff)


def test_stale_cleanup_fails_closed_without_source_endpoint() -> None:
    with pytest.raises(ValueError, match="stale_source_endpoint"):
        crawler.save_db(
            [sample_row()],
            stale_provider="TEST_PROVIDER",
            stale_cutoff=datetime.now().astimezone(),
        )
