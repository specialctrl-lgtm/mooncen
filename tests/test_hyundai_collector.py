from __future__ import annotations

from bs4 import BeautifulSoup

from tools import sample_collect_from_yaml as collector


class FakeResponse:
    def __init__(self, page: int) -> None:
        self.text = (
            '<div class="paging"><a href="?page=220">220</a></div>'
            f'<span id="page">{page}</span>'
        )

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.pages: list[int] = []

    def get(self, _url: str, *, params: dict[str, str], timeout: int) -> FakeResponse:
        assert timeout == 20
        page = int(params["page"])
        self.pages.append(page)
        return FakeResponse(page)


def test_hyundai_collector_finishes_all_pages_with_bounded_details(monkeypatch) -> None:
    fake_session = FakeSession()
    detail_calls: list[str] = []
    monkeypatch.setattr(collector, "session", lambda: fake_session)
    monkeypatch.setattr(collector, "HYUNDAI_DETAIL_LIMIT", 2)

    def list_rows(soup: BeautifulSoup) -> list[dict[str, str]]:
        page = soup.select_one("#page").get_text(strip=True)
        return [
            {
                "provider": "HYUNDAI_DEPT",
                "provider_course_id": page,
                "title": f"강좌 {page}",
                "branch": "본점",
                "raw_url": f"https://example.com/{page}",
                "target": "성인",
                "fee": "10,000원",
                "period": "2026.08.01 ~ 2026.08.31",
                "category": "성인",
                "schedule_raw": "월 10:00-11:00",
            }
        ]

    monkeypatch.setattr(collector, "hyundai_list_rows", list_rows)
    monkeypatch.setattr(
        collector,
        "hyundai_detail_fields",
        lambda _session, url: detail_calls.append(url) or {"room": "문화홀"},
    )

    rows, pages, note = collector.hyundai(100_000)

    assert len(rows) == 220
    assert pages == 220
    assert fake_session.pages == list(range(1, 221))
    assert len(detail_calls) == 2
    assert rows[0]["room"] == "문화홀"
    assert "total_pages_hint=220" in note
    assert "detail_attempts=2" in note
    assert "snapshot_complete=true" in note


def test_hyundai_request_budget_covers_list_and_detail_caps() -> None:
    assert collector.PROVIDER_COLLECTOR_REQUEST_BUDGETS["HYUNDAI_DEPT"] >= (
        collector.HYUNDAI_MAX_PAGES + collector.HYUNDAI_DETAIL_LIMIT
    )
