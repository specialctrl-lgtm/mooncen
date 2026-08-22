from __future__ import annotations

import calendar
from collections import Counter
from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from Crawler import Crawler_MunicipalYaml as router
from Crawler import municipal_pohang_forest_experience as collector


@dataclass
class _Response:
    url: str
    content: bytes
    status_code: int = 200

    @property
    def headers(self) -> dict[str, str]:
        return {"content-type": "text/html; charset=UTF-8"}

    @property
    def history(self) -> tuple[Any, ...]:
        return ()


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def post(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the audited collector must never POST")


def _target() -> dict[str, str]:
    return {
        "provider": collector.POHANG_FOREST_EXPERIENCE_PROVIDER,
        "url": collector.POHANG_FOREST_EXPERIENCE_URL,
    }


def _programs() -> tuple[Any, ...]:
    return tuple(
        collector._Program(
            month=month,
            title=f"Hands-on forest programme {month}",
            activity="making",
            play="nature play",
            materials="natural materials",
            hands_on_evidence="making",
        )
        for month in collector.POHANG_FOREST_EXPERIENCE_OPERATION_MONTHS
    )


def _page(year: int, month: int, *, drift: bool = False) -> Any:
    days = []
    for number in range(1, calendar.monthrange(year, month)[1] + 1):
        value = date(year, month, number)
        if value.weekday() >= 5:
            source_status, status, mode, control = "UNAVAILABLE", "", "", False
        elif month == 8 and number < 5:
            source_status, status, mode, control = "", "", "", False
        elif month == 8 and number <= 7:
            source_status, status, mode, control = "CLOSED", "CLOSED", "2", True
        else:
            source_status, status, mode, control = "OPEN", "OPEN", "4", True
        days.append(
            collector._CalendarDay(
                value=value,
                source_status=source_status,
                status=status,
                control_mode=mode,
                application_control=control,
            )
        )
    if drift:
        first = days[0]
        days[0] = collector._CalendarDay(
            value=first.value,
            source_status="OPEN",
            status="OPEN",
            control_mode="4",
            application_control=True,
        )
    return collector._MonthPage(
        year=year,
        month=month,
        days=tuple(days),
        programs=_programs(),
        venue=collector._VENUE_TEXT,
    )


def _fixture_collect(
    monkeypatch: pytest.MonkeyPatch,
    *,
    max_pages: int = 10,
    detail_limit: int = 120,
    unstable_boundary: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], list[str], _Session]:
    calls: list[str] = []
    parse_counts: Counter[int] = Counter()
    current_session = _Session()

    def fetcher(_session: Any, url: str, _timeout: int) -> _Response:
        calls.append(url)
        body = f"<html><head><title>{collector._PAGE_TITLE}</title></head></html>"
        return _Response(url=url, content=body.encode("utf-8"))

    def parse_page(
        _soup: Any, *, expected_year: int, expected_month: int
    ) -> Any:
        parse_counts[expected_month] += 1
        drift = (
            unstable_boundary
            and expected_month == 8
            and parse_counts[expected_month] == 2
        )
        return _page(expected_year, expected_month, drift=drift)

    monkeypatch.setattr(collector, "_parse_month_page", parse_page)
    rows, parser, meta = collector.collect_pohang_forest_experience(
        _target(),
        today="2026-08-05",
        max_pages=max_pages,
        detail_limit=detail_limit,
        session_factory=lambda: current_session,
        fetcher=fetcher,
    )
    return rows, parser, meta, calls, current_session


def test_public_get_allowlist_is_exact() -> None:
    canonical = collector.pohang_forest_experience_month_url(2026, 8)
    assert collector._request_kind(canonical) == "list"
    forbidden = (
        canonical.replace("https://", "http://"),
        canonical.replace("www.gb.go.kr", "evil.example"),
        canonical.replace("https://", "https://member@"),
        canonical + "&mode=login",
        canonical.replace("initMonth=8", "initMonth=13"),
        canonical + "#application",
    )
    for url in forbidden:
        with pytest.raises(collector.PohangForestExperienceContractError):
            collector._request_kind(url)


def test_complete_current_snapshot_is_north_district_only_and_get_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, parser, meta, calls, current_session = _fixture_collect(monkeypatch)
    assert parser == collector.POHANG_FOREST_EXPERIENCE_PARSER
    assert len(rows) == 84
    assert Counter(row["status"] for row in rows) == {"OPEN": 81, "CLOSED": 3}
    assert Counter(row["start_date"][:7] for row in rows) == {
        "2026-08": 19,
        "2026-09": 22,
        "2026-10": 22,
        "2026-11": 21,
    }
    assert {row["municipality_code"] for row in rows} == {"4711300000"}
    assert {row["municipality_name"] for row in rows} == {
        collector.POHANG_FOREST_EXPERIENCE_MUNICIPALITY_NAME
    }
    assert {row["venue"] for row in rows} == {
        collector.POHANG_FOREST_EXPERIENCE_VENUE
    }
    assert all(row["application_url"] == "" for row in rows)
    assert all(row["raw_fields"]["application_form_not_submitted"] for row in rows)
    assert meta["logical_requests"] == meta["get_requests"] == 8
    assert meta["list_requests"] == 8 and meta["detail_requests"] == 0
    assert meta["post_requests"] == 0
    assert meta["sentinel_month"] == 12
    assert meta["sentinel_shell_control_count"] == 23
    assert meta["snapshot_complete"] is True
    assert not meta["errors"]
    assert [int(parse_qs(urlparse(url).query)["initMonth"][0]) for url in calls] == [
        8,
        9,
        10,
        11,
        12,
        8,
        11,
        12,
    ]
    assert all(urlparse(url).scheme == "https" for url in calls)
    assert current_session.closed is True


@pytest.mark.parametrize(
    ("max_pages", "detail_limit", "message", "expected_calls"),
    (
        (7, 120, "below required 8", 0),
        (10, 83, "partial dated experience snapshot", 8),
    ),
)
def test_caps_fail_atomically(
    monkeypatch: pytest.MonkeyPatch,
    max_pages: int,
    detail_limit: int,
    message: str,
    expected_calls: int,
) -> None:
    rows, _, meta, calls, current_session = _fixture_collect(
        monkeypatch, max_pages=max_pages, detail_limit=detail_limit
    )
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False
    assert len(calls) == expected_calls
    assert current_session.closed is (expected_calls > 0)


def test_boundary_instability_fails_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, _, meta, calls, current_session = _fixture_collect(
        monkeypatch, unstable_boundary=True
    )
    assert rows == []
    assert "stability recheck changed" in meta["configured_collection_error"]
    assert len(calls) == 6
    assert current_session.closed is True


def test_router_dispatches_exact_target_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    def fake_collect(target: Any, **kwargs: Any):
        calls.append((target, kwargs))
        return [{"provider": collector.POHANG_FOREST_EXPERIENCE_PROVIDER}], "fixture", {"ok": True}

    monkeypatch.setattr(collector, "collect_pohang_forest_experience", fake_collect)
    target = router.CrawlTarget(
        provider=collector.POHANG_FOREST_EXPERIENCE_PROVIDER,
        name="Pohang forest experience",
        branch=collector.POHANG_FOREST_EXPERIENCE_BRANCH,
        url=collector.POHANG_FOREST_EXPERIENCE_URL,
        source="test",
        priority=1,
        region=collector.POHANG_FOREST_EXPERIENCE_MUNICIPALITY_NAME,
        extra={},
    )
    rows, parser, meta = router.collect_from_url(
        target, timeout=3, max_depth=0, max_pages=10, detail_limit=120
    )
    assert rows and parser == "fixture" and meta == {"ok": True}
    assert len(calls) == 1
    assert calls[0][1]["session_factory"] is router.session


def test_single_public_target_entry() -> None:
    root = Path(__file__).resolve().parents[1]
    registry = yaml.safe_load(
        (root / "config/crawl_targets/public_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )
    matches = [
        item
        for item in registry["targets"]
        if item.get("provider") == collector.POHANG_FOREST_EXPERIENCE_PROVIDER
    ]
    assert len(matches) == 1
    target = matches[0]
    assert target["candidate_id"] == collector.POHANG_FOREST_EXPERIENCE_CANDIDATE_ID
    assert target["url"] == collector.POHANG_FOREST_EXPERIENCE_URL
    assert target["crawler_module"] == "Crawler.municipal_pohang_forest_experience"
    assert target["crawler_callable"] == "collect_pohang_forest_experience"
    assert target["municipality_code"] == "4711300000"
    assert target["covered_municipalities"] == [
        {
            "code": "4711300000",
            "sido": "경상북도",
            "sigungu": "포항시 북구",
            "full_name": "경상북도 포항시 북구",
        }
    ]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_POHANG_FOREST_EXPERIENCE") != "1",
    reason="set RUN_LIVE_POHANG_FOREST_EXPERIENCE=1 for the official GET-only contract",
)
def test_live_exact_snapshot() -> None:
    rows, _, meta = collector.collect_pohang_forest_experience(
        _target(), today="2026-08-05", max_pages=10, detail_limit=120
    )
    assert len(rows) == 84 and meta["snapshot_complete"] is True
    assert meta["status_counts"] == {"CLOSED": 3, "OPEN": 81}
    assert meta["post_requests"] == meta["detail_requests"] == 0
