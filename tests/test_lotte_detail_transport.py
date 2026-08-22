from __future__ import annotations

from datetime import date

import requests

from Crawler import Crawler_Lotte as lotte


DETAIL_URL = (
    "https://culture.lotteshopping.com/application/search/view.do"
    "?brchCd=0014&yy=2026&lectSmsterCd=2&lectCd=0711"
)

DETAIL_HTML = """
<html>
  <body>
    <p class="tit lectNm">Hands-on science</p>
    <span class="tcNm">Teacher</span>
    <dd class="objClNm">Ages 8-10</dd>
    <dd class="lectClNm">Special lecture</dd>
    <dd class="lectAmt">10,000</dd>
    <dd class="lectTime">(Tue) 15:00~17:00</dd>
    <dd class="lectStDtm">2026.08.18 ~ 2026.08.18</dd>
    <span class="rceptPrdStDt">2026.07.01~2026.08.16</span>
    <p class="label lectStatNm">Open</p>
  </body>
</html>
"""

UNAVAILABLE_DETAIL_HTML = """
<html>
  <body>
    <p>요청하신 페이지가 존재하지 않거나, 일시적인 시스템 장애입니다.</p>
    <p>해당 서비스의 주소가 이동, 삭제되었거나 접속 장애 입니다.</p>
  </body>
</html>
"""


class _Response:
    def __init__(self, text: str = DETAIL_HTML) -> None:
        self.text = text
        self.encoding = None

    def raise_for_status(self) -> None:
        return None


def test_lotte_detail_uses_static_http_before_browser(monkeypatch) -> None:
    crawler = lotte.LotteCrawler()
    browser_calls: list[str] = []
    monkeypatch.setattr(
        crawler,
        "_http_request_with_retry",
        lambda *_args, **_kwargs: _Response(),
    )
    monkeypatch.setattr(
        crawler,
        "_get_page",
        lambda url, **_kwargs: browser_calls.append(url) or None,
    )
    try:
        course = crawler.scrape_course_detail(DETAIL_URL)
    finally:
        crawler.http_session.close()

    assert isinstance(course, dict)
    assert course["provider_course_id"] == "0014-2026-2-0711"
    assert course["category_raw"] == "Special lecture"
    assert course["apply_start"].isoformat() == "2026-07-01"
    assert course["apply_end"].isoformat() == "2026-08-16"
    assert crawler._detail_http_success_count == 1
    assert crawler._detail_browser_fallback_count == 0
    assert browser_calls == []


def test_lotte_detail_falls_back_to_browser_after_http_failure(monkeypatch) -> None:
    crawler = lotte.LotteCrawler()
    monkeypatch.setattr(
        crawler,
        "_http_request_with_retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.Timeout()),
    )
    monkeypatch.setattr(crawler, "_get_page", lambda *_args, **_kwargs: DETAIL_HTML)
    try:
        course = crawler.scrape_course_detail(DETAIL_URL)
    finally:
        crawler.http_session.close()

    assert isinstance(course, dict)
    assert crawler._detail_http_success_count == 0
    assert crawler._detail_browser_fallback_count == 1


def test_lotte_terminal_unavailable_detail_is_tolerated_only_at_low_ratio(
    monkeypatch,
) -> None:
    crawler = lotte.LotteCrawler()
    monkeypatch.setattr(
        crawler,
        "_http_request_with_retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            requests.HTTPError()
        ),
    )
    monkeypatch.setattr(
        crawler,
        "_get_page",
        lambda *_args, **_kwargs: UNAVAILABLE_DETAIL_HTML,
    )
    try:
        assert crawler.scrape_course_detail(DETAIL_URL) is None
    finally:
        crawler.http_session.close()

    assert crawler.had_errors is False
    assert crawler.crawl_complete is True
    assert crawler._terminal_unavailable_detail_count == 1
    crawler._detail_http_success_count = 999
    assert crawler._terminal_unavailable_detail_is_tolerable() is True

    crawler._terminal_unavailable_detail_count = 2
    assert crawler._terminal_unavailable_detail_is_tolerable() is False
    crawler._terminal_unavailable_detail_count = 6
    crawler._detail_http_success_count = 9_994
    assert crawler._terminal_unavailable_detail_is_tolerable() is False


def test_lotte_cached_raw_url_identity_avoids_per_row_lookup() -> None:
    crawler = lotte.LotteCrawler()
    crawler._existing_course_ids_by_raw_url = {DETAIL_URL: "stable-id"}
    course = {
        "raw_url": f"{DETAIL_URL}&mooncen_course_id=legacy",
        "provider_course_id": "new-id",
    }
    try:
        assert crawler._coalesce_course_identity_from_cache(course) is True
    finally:
        crawler.http_session.close()

    assert course["raw_url"] == DETAIL_URL
    assert course["provider_course_id"] == "stable-id"
    assert crawler._cached_identity_reuse_count == 1


def test_lotte_branch_ajax_paginates_and_skips_ended_courses(monkeypatch) -> None:
    crawler = lotte.LotteCrawler()
    responses = iter(
        [
            _Response(
                """
                <form id="searchBranch">
                  <input name="orderSet" value="C">
                </form>
                """
            ),
            _Response(
                """
                <div class="card_list_v" data-tot-cnt="2">
                  <a class="lec_list"
                     href="/application/search/view.do?brchCd=0014&amp;yy=2026&amp;lectSmsterCd=3&amp;lectCd=0001">
                    <div class="label_div"><p class="label">Open</p></div>
                    <p class="tit">Current course</p>
                  </a>
                </div>
                <div class="card_list_v" data-tot-cnt="2">
                  <a class="lec_list"
                     href="/application/search/view.do?brchCd=0014&amp;yy=2026&amp;lectSmsterCd=2&amp;lectCd=0002">
                    <div class="label_div"><p class="label">강의종료</p></div>
                    <p class="tit">Ended course</p>
                  </a>
                </div>
                """
            ),
        ]
    )
    monkeypatch.setattr(
        crawler,
        "_http_request_with_retry",
        lambda *_args, **_kwargs: next(responses),
    )
    try:
        courses = crawler.scrape_branch_courses(
            {"branch_code": "0014", "name": "Pohang"}
        )
    finally:
        crawler.http_session.close()

    assert [course["title"] for course in courses] == ["Current course"]
    assert courses[0]["source"] == "lotte_branch_ajax"
    assert courses[0]["list_status_raw"] == "Open"


def test_lotte_branch_ajax_retries_transient_failure(monkeypatch) -> None:
    crawler = lotte.LotteCrawler()
    calls = 0
    browser_calls: list[str] = []

    def scrape_by_query(_branch, _query, limit=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            crawler.crawl_complete = False
            return []
        return [{"url": DETAIL_URL, "title": "Recovered course"}]

    monkeypatch.setattr(
        lotte,
        "LOTTE_BRANCH_RETRY_DELAYS_SECONDS",
        (0.0, 0.0),
    )
    monkeypatch.setattr(crawler, "scrape_branch_courses_by_query", scrape_by_query)
    monkeypatch.setattr(
        crawler,
        "_scrape_branch_courses_with_browser",
        lambda *_args, **_kwargs: browser_calls.append("browser") or [],
    )
    try:
        courses = crawler.scrape_branch_courses(
            {"branch_code": "0026", "name": "Mia"}
        )
    finally:
        crawler.http_session.close()

    assert [course["title"] for course in courses] == ["Recovered course"]
    assert calls == 2
    assert browser_calls == []
    assert crawler.crawl_complete is True
    assert crawler.had_errors is False


def test_lotte_parallel_details_preserve_order_and_retry_failures(
    monkeypatch,
) -> None:
    crawler = lotte.LotteCrawler()
    monkeypatch.setenv("LOTTE_DETAIL_WORKERS", "2")
    monkeypatch.setenv("LOTTE_DETAIL_REQUEST_DELAY_SECONDS", "0")
    calls: list[tuple[str, bool]] = []

    def scrape_detail(self, url: str, *, browser_fallback: bool = True):
        calls.append((url, browser_fallback))
        if url.endswith("0002") and not browser_fallback:
            return None
        return {"raw_url": url}

    monkeypatch.setattr(lotte.LotteCrawler, "scrape_course_detail", scrape_detail)
    course_list = [
        {"url": f"{DETAIL_URL[:-4]}{course_id}"}
        for course_id in ("0001", "0002", "0003")
    ]
    try:
        results = crawler.scrape_course_details(course_list)
    finally:
        crawler.http_session.close()

    assert [result["raw_url"] for result in results] == [
        course["url"] for course in course_list
    ]
    failed_url = course_list[1]["url"]
    assert (failed_url, False) in calls
    assert (failed_url, True) in calls


def test_lotte_test_mode_prefetches_only_twenty_details(monkeypatch) -> None:
    crawler = lotte.LotteCrawler()
    prefetched_counts: list[int] = []
    branch = {"branch_code": "0014", "name": "Pohang"}
    course_list = [
        {"url": f"{DETAIL_URL}&sample={index}", "title": f"Course {index}"}
        for index in range(100)
    ]
    monkeypatch.setattr(crawler, "_init_driver", lambda: None)
    monkeypatch.setattr(crawler, "_close_driver", lambda: None)
    monkeypatch.setattr(crawler, "scrape_real_branches", lambda: [branch])
    monkeypatch.setattr(crawler, "save_branch", lambda *_args: "branch-id")
    monkeypatch.setattr(
        crawler,
        "_load_existing_course_ids_by_raw_url",
        lambda: {},
    )
    monkeypatch.setattr(crawler, "scrape_notice_course_map", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        crawler,
        "scrape_branch_courses",
        lambda *_args, **_kwargs: list(course_list),
    )

    def scrape_details(rows):
        prefetched_counts.append(len(rows))
        return [{"branch_code": "0014"} for _row in rows]

    monkeypatch.setattr(crawler, "scrape_course_details", scrape_details)
    monkeypatch.setattr(crawler, "save_course", lambda *_args: True)
    monkeypatch.setattr(crawler, "monitor_reception_notices", lambda *_args: {})

    assert crawler.run(test_mode=True) is True
    assert prefetched_counts == [20]


def test_lotte_group_option_overrides_date_time_sessions_and_target() -> None:
    crawler = lotte.LotteCrawler()
    base = {
        "provider_course_id": "0002-2026-2-0579",
        "branch_code": "0002",
        "raw_url": DETAIL_URL,
        "schedule_raw": "금 11:00~12:00",
        "start_date": date(2026, 7, 1),
        "end_date": date(2026, 8, 31),
        "target": "1인강좌",
    }
    try:
        dated = crawler._apply_group_course_option(
            base,
            {
                "branch_code": "0002",
                "year": "2026",
                "semester": "2",
                "lect_cd": "0580",
                "option_text": "07/31(금) 12:20~13:20 12:20",
            },
        )
        age_group = crawler._apply_group_course_option(
            base,
            {
                "branch_code": "0002",
                "year": "2026",
                "semester": "3",
                "lect_cd": "0388",
                "option_text": "화 11:20~12:00 12회 7~14개월",
            },
        )
    finally:
        crawler.http_session.close()

    assert dated["provider_course_id"] == "0002-2026-2-0580"
    assert dated["schedule_raw"] == "금 12:20~13:20"
    assert dated["start_date"] == date(2026, 7, 31)
    assert dated["end_date"] == date(2026, 7, 31)
    assert age_group["schedule_raw"] == "화 11:20~12:00 12회"
    assert age_group["target"] == "7~14개월"
