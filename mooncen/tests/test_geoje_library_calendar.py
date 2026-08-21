from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as crawler


STATIC_DISCOVERY_URL = (
    "https://lib.geoje.go.kr/com/requestPage.do?selMenuNo=104010000"
    "&returnUrl=/culture/d010200.do?year=2026&month=5"
)


def _calendar_coordinates(url: str) -> tuple[int, int]:
    query = parse_qs(urlparse(url).query)
    nested = parse_qs(urlparse(query["returnUrl"][0]).query)
    return int(nested["year"][0]), int(query["month"][0])


def test_geoje_calendar_uses_current_month_not_static_discovery_month() -> None:
    assert crawler.geoje_calendar_start_year_month(
        STATIC_DISCOVERY_URL,
        reference_date=date(2026, 7, 23),
    ) == (2026, 6)


def test_geoje_calendar_window_rolls_across_year_boundary(monkeypatch) -> None:
    requested_urls: list[str] = []

    def fake_fetch_soup(_session, url: str, *, timeout: int):
        assert timeout == 5
        requested_urls.append(url)
        return BeautifulSoup("<table class='calendar'></table>", "lxml")

    monkeypatch.setattr(crawler, "fetch_soup", fake_fetch_soup)
    target = SimpleNamespace(url=STATIC_DISCOVERY_URL)

    rows, pages, detail_pages = crawler.collect_geoje_calendar_events(
        target,
        SimpleNamespace(),
        timeout=5,
        max_months=3,
        reference_date=date(2026, 12, 15),
    )

    assert rows == []
    assert pages == 3
    assert detail_pages == 0
    assert [_calendar_coordinates(url) for url in requested_urls] == [
        (2026, 11),
        (2027, 0),
        (2027, 1),
    ]


def test_geoje_empty_course_sentinel_marks_full_snapshot_complete(monkeypatch) -> None:
    class EmptyResponse:
        text = "<table class='tbl-type01'><tbody></tbody></table>"
        encoding = "utf-8"

        @staticmethod
        def raise_for_status() -> None:
            return None

    class EmptySession:
        calls = 0

        def post(self, _url: str, *, data: dict[str, str], timeout: int):
            assert data["currentPageNo"] == "1"
            assert timeout == 5
            self.calls += 1
            return EmptyResponse()

    client = EmptySession()
    monkeypatch.setattr(crawler, "session", lambda: client)
    monkeypatch.setattr(
        crawler,
        "collect_geoje_calendar_events",
        lambda *args, **kwargs: ([], 6, 0),
    )
    target = SimpleNamespace(
        provider="MUNI_LIB_GEOJE_GO_KR_401A2022",
        url=STATIC_DISCOVERY_URL,
    )

    rows, parser, meta = crawler.collect_geoje_library_courses(
        target,
        timeout=5,
        max_pages=3,
    )

    assert rows == []
    assert parser == "geoje_library_calendar+course_table"
    assert client.calls == 1
    assert meta["pagination_exhausted"] is True
    assert meta["pagination_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"] == (
        "no_current_geoje_library_courses_or_calendar_events"
    )
