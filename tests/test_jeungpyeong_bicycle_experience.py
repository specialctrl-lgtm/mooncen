from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from Crawler import Crawler_MunicipalYaml as router
from Crawler import municipal_jeungpyeong_bicycle_experience as collector


@dataclass
class _Response:
    payload: Any = None
    text: str = ""
    url: str = ""
    content_type: str = "application/json"
    status_code: int = 200

    @property
    def content(self) -> bytes:
        if self.text:
            return self.text.encode("utf-8")
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

    @property
    def headers(self) -> dict[str, str]:
        return {"content-type": self.content_type}

    def json(self) -> Any:
        return self.payload


def _info_html() -> str:
    return """
    <html><body>
      자전거 교통안전교육장 운영기간 3월~12월
      교육시간 10:00 13:00 자전거타기 실습
      충청북도 증평군 증평읍 남하용강로 16
    </body></html>
    """


def _shell_html() -> str:
    return """
    <html><body>
      예약정원(40명)
      <script>
      /kor/prog/bcyclParkResve/sub05_03_09_02/getTime.do
      /kor/prog/bcyclParkResve/sub05_03_09_02/getCalendar.do
      </script>
      <form id="searchForm"
        action="/kor/prog/bcyclParkResve/sub05_03_09_02/write.do"></form>
    </body></html>
    """


def _slots(first: str = "40,D", second: str = "40,D") -> dict[str, str]:
    return {"10:00": first, "13:00": second}


def _calendar(year: int, month: int) -> dict[str, dict[str, Any]]:
    length = calendar.monthrange(year, month)[1]
    result: dict[str, dict[str, Any]] = {}
    for day in range(1, length + 1):
        current = date(year, month, day)
        source_type = "D"
        slots = _slots()
        if year == 2026 and month == 8:
            if day < 6:
                source_type = "C"
                slots = {}
            elif day == 6:
                source_type = "A"
            elif day == 7:
                source_type = "A"
                slots = _slots("40,D", "31,S")
            elif day == 10:
                source_type = "B"
        item: dict[str, Any] = {
            "D": str((current.weekday() + 1) % 7),
            "length": str(length),
            "DT": current.isoformat(),
            "DD": f"{day:02d}",
            "type": source_type,
        }
        if slots:
            item["nmpr"] = slots
        if source_type == "B":
            item["closeType"] = "휴관"
        result[str(day)] = item
    return result


class _Session:
    def __init__(self, *, drift: bool = False) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.closed = False
        self.drift = drift
        self.time_calls = 0

    def request(self, method: str, url: str, **kwargs: Any) -> _Response:
        payload = dict(kwargs.get("data") or {})
        self.calls.append((method, url, payload))
        if method == "GET" and url == collector.JEUNGPYEONG_BICYCLE_INFO_URL:
            return _Response(
                text=_info_html(),
                url=url,
                content_type="text/html; charset=UTF-8",
            )
        if method == "GET" and url == collector.JEUNGPYEONG_BICYCLE_URL:
            return _Response(
                text=_shell_html(),
                url=url,
                content_type="text/html; charset=UTF-8",
            )
        if method == "POST" and url == collector.JEUNGPYEONG_BICYCLE_TIME_URL:
            self.time_calls += 1
            value = [{"resve_time": "10:00"}, {"resve_time": "13:00"}]
            if self.drift and self.time_calls == 2:
                value = [{"resve_time": "10:00"}, {"resve_time": "14:00"}]
            return _Response(payload=value, url=url, content_type="text/html;charset=UTF-8")
        if method == "POST" and url == collector.JEUNGPYEONG_BICYCLE_CALENDAR_URL:
            year_month = payload["yearMonth"]
            return _Response(
                payload=_calendar(int(year_month[:4]), int(year_month[4:])),
                url=url,
                content_type="text/html;charset=UTF-8",
            )
        raise AssertionError(f"unsafe or unexpected request: {method} {url} {payload}")

    def close(self) -> None:
        self.closed = True


def _target() -> dict[str, str]:
    return {
        "provider": collector.JEUNGPYEONG_BICYCLE_PROVIDER,
        "url": collector.JEUNGPYEONG_BICYCLE_URL,
    }


def test_provider_candidate_and_target_are_exact_url_contracts() -> None:
    url = collector.JEUNGPYEONG_BICYCLE_URL
    assert collector.JEUNGPYEONG_BICYCLE_PROVIDER == (
        "MUNI_WWW_JP_GO_KR_" + hashlib.sha1(url.encode()).hexdigest()[:8].upper()
    )
    assert collector.JEUNGPYEONG_BICYCLE_CANDIDATE_ID == (
        "MUNI_IR_" + hashlib.sha256(url.encode()).hexdigest()[:12].upper()
    )
    assert collector.is_jeungpyeong_bicycle_experience_target(_target())
    assert not collector.is_jeungpyeong_bicycle_experience_target(
        {**_target(), "provider": "MUNI_WRONG"}
    )
    assert not collector.is_jeungpyeong_bicycle_experience_target(
        {**_target(), "url": collector.JEUNGPYEONG_BICYCLE_INFO_URL}
    )


def test_complete_season_snapshot_uses_only_public_read_routes() -> None:
    session = _Session()
    rows, parser, meta = collector.collect_jeungpyeong_bicycle_experience(
        _target(),
        today="2026-08-05",
        timeout=3,
        max_pages=12,
        detail_limit=500,
        session_factory=lambda: session,
    )

    assert parser == collector.JEUNGPYEONG_BICYCLE_PARSER
    assert rows and meta["snapshot_complete"] is True
    assert meta["source_day_count"] == 153
    assert meta["data_months"] == 5
    assert meta["sentinel_pages"] == 1
    assert meta["stable_rechecks"] == 6
    assert meta["details_complete"] is True
    assert meta["classification_complete"] is True
    assert meta["experience_rows"] == len(rows)
    assert meta["status_counts"]["OPEN"] >= 1
    assert meta["status_counts"]["CLOSED"] >= 1
    assert meta["status_counts"]["SCHEDULED"] >= 1
    assert all(row["service_family"] == "experience" for row in rows)
    assert all(
        row["provider_course_id"].startswith(
            f"{collector.JEUNGPYEONG_BICYCLE_PROVIDER}:slot:"
        )
        for row in rows
    )
    assert all(bool(row["application_url"]) == row["reservation_available"] for row in rows)
    assert all(
        not row["application_url"]
        or row["application_url"] == collector.JEUNGPYEONG_BICYCLE_URL
        for row in rows
    )
    requested = "\n".join(url.lower() for _, url, _ in session.calls)
    assert "/write.do" not in requested
    assert "/sub05_03_09_03/list.do" not in requested
    assert not any(
        marker in requested
        for marker in ("/login", "/auth", "/member", "/applicant", "/download")
    )
    assert session.closed is True


def test_stability_drift_fails_atomically_and_closes_session() -> None:
    session = _Session(drift=True)
    rows, _, meta = collector.collect_jeungpyeong_bicycle_experience(
        _target(),
        today="2026-08-05",
        timeout=3,
        max_pages=12,
        detail_limit=500,
        session_factory=lambda: session,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "official programme times changed" in meta["configured_collection_error"]
    assert session.closed is True


def test_router_dispatches_exact_experience_target(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_collect(target: Any, **kwargs: Any) -> tuple[list[dict[str, str]], str, dict[str, bool]]:
        calls.append(kwargs)
        return ([{"provider": collector.JEUNGPYEONG_BICYCLE_PROVIDER}], "fixture", {"ok": True})

    monkeypatch.setattr(
        collector,
        "collect_jeungpyeong_bicycle_experience",
        fake_collect,
    )
    target = router.CrawlTarget(
        provider=collector.JEUNGPYEONG_BICYCLE_PROVIDER,
        name="fixture",
        branch="fixture",
        url=collector.JEUNGPYEONG_BICYCLE_URL,
        source="fixture",
        priority=1,
        region="충청북도 증평군",
        extra={},
    )
    rows, parser, meta = router.collect_from_url(target, timeout=3, max_pages=12, detail_limit=500)
    assert rows == [{"provider": collector.JEUNGPYEONG_BICYCLE_PROVIDER}]
    assert parser == "fixture" and meta == {"ok": True}
    assert len(calls) == 1
    assert calls[0]["timeout"] == 3
    assert calls[0]["max_pages"] == 12
    assert calls[0]["detail_limit"] == 500


def test_target_operational_and_coverage_configuration() -> None:
    root = Path(__file__).resolve().parents[1]
    targets = yaml.safe_load(
        (root / "config/crawl_targets/public_reservation.yaml").read_text(encoding="utf-8")
    )["targets"]
    target = next(
        row for row in targets if row.get("provider") == collector.JEUNGPYEONG_BICYCLE_PROVIDER
    )
    assert target["url"] == collector.JEUNGPYEONG_BICYCLE_URL
    assert target["candidate_id"] == collector.JEUNGPYEONG_BICYCLE_CANDIDATE_ID
    assert target["ops_scopes"] == ["experience"]
    assert target["covered_municipalities"] == [
        {
            "code": "4374500000",
            "sido": "충청북도",
            "sigungu": "증평군",
            "full_name": "충청북도 증평군",
        }
    ]
    operational = yaml.safe_load(
        (root / "config/municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )["entries"]
    source = next(
        row for row in operational if row.get("provider") == collector.JEUNGPYEONG_BICYCLE_PROVIDER
    )
    assert source["ops_scopes"] == ["experience"]
    coverage = yaml.safe_load(
        (root / "config/municipal_integrated_reservation_coverage.yaml").read_text(
            encoding="utf-8"
        )
    )["municipalities"]
    region = next(row for row in coverage if row.get("code") == "4374500000")
    assert collector.JEUNGPYEONG_BICYCLE_PROVIDER in region["owner_providers"]
    assert any(
        row.get("kind") == "exact_active_url"
        and row.get("provider") == collector.JEUNGPYEONG_BICYCLE_PROVIDER
        and row.get("target_file") == "public_reservation.yaml"
        for row in region["evidence"]
    )


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_JEUNGPYEONG_BICYCLE_EXPERIENCE") != "1",
    reason="set RUN_LIVE_JEUNGPYEONG_BICYCLE_EXPERIENCE=1 for official live contract test",
)
def test_live_jeungpyeong_bicycle_snapshot() -> None:
    rows, _, meta = collector.collect_jeungpyeong_bicycle_experience(
        _target(),
        today="2026-08-05",
        timeout=30,
        max_pages=12,
        detail_limit=500,
    )
    assert meta["snapshot_complete"] is True
    assert meta["source_day_count"] == 153
    assert len(rows) >= 200
    assert meta["experience_rows"] == len(rows)
    assert all(bool(row["application_url"]) == row["reservation_available"] for row in rows)
