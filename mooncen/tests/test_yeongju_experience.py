from __future__ import annotations

from calendar import Calendar
from collections import Counter
from dataclasses import dataclass
from html import escape
import os
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
import yaml

from Crawler import municipal_yeongju_experience as yeongju
from tools.promote_municipal_integrated_reservation_targets import (
    candidate_id,
    normalized_duplicate_url,
    stable_provider,
)


ROOT = Path(__file__).resolve().parents[1]


def _target(**overrides: Any) -> dict[str, Any]:
    return {
        "provider": yeongju.YEONGJU_EXPERIENCE_PROVIDER,
        "url": yeongju.YEONGJU_EXPERIENCE_URL,
        "name": "영주시 예약통합서비스 실내놀이터",
        "branch": yeongju.YEONGJU_EXPERIENCE_BRANCH,
        **overrides,
    }


def _calendar_control(
    partition: str,
    service_date: str,
    service_time: str,
    current: int,
    total: int,
    *,
    control_partition: Optional[str] = None,
) -> str:
    query = urlencode(
        (
            ("mnu_uid", yeongju.YEONGJU_EXPERIENCE_MENU_ID),
            ("code_uid", yeongju.YEONGJU_EXPERIENCE_CODE_ID),
            ("reserve_uid", control_partition or partition),
            ("cmd", "4"),
            ("apply_date", service_date),
            ("apply_time", service_time),
        )
    )
    href = f"javascript:fnPopupAuth('{yeongju.YEONGJU_EXPERIENCE_PATH}?{query}')"
    return f'<a href="{escape(href, quote=True)}">{service_time} ({current}/{total})</a>'


def _page(
    year: int,
    month: int,
    partition: str,
    slots: Mapping[int, list[tuple[str, str, Optional[int], Optional[int]]]],
    *,
    control_partition: Optional[str] = None,
    unknown_status: bool = False,
) -> str:
    tab_items: list[str] = []
    for item in yeongju.YEONGJU_EXPERIENCE_PARTITIONS:
        query = urlencode(
            (
                ("cmd", "1"),
                ("apply_date", ""),
                ("code_uid", yeongju.YEONGJU_EXPERIENCE_CODE_ID),
                ("apply_time", ""),
                ("listType", ""),
                ("mnu_uid", yeongju.YEONGJU_EXPERIENCE_MENU_ID),
                ("reserve_uid", item.code),
            )
        )
        active = ' class="on"' if item.code == partition else ""
        tab_items.append(
            f'<li{active}><a href="?{escape(query, quote=True)}">'
            f"<span>{item.label}</span></a></li>"
        )

    def year_link(target_year: int, css: str) -> str:
        query = urlencode(
            (
                ("apply_date", ""),
                ("code_uid", yeongju.YEONGJU_EXPERIENCE_CODE_ID),
                ("apply_time", ""),
                ("listType", ""),
                ("mnu_uid", yeongju.YEONGJU_EXPERIENCE_MENU_ID),
                ("reserve_uid", partition),
                ("initYear", target_year),
                ("initMonth", 1),
            )
        )
        return (
            f'<a class="{css}" href="?{escape(query, quote=True)}">'
            f"{target_year}년</a>"
        )

    month_links: list[str] = []
    for current_month in range(1, 13):
        query = urlencode(
            (
                ("apply_date", ""),
                ("code_uid", yeongju.YEONGJU_EXPERIENCE_CODE_ID),
                ("apply_time", ""),
                ("listType", ""),
                ("mnu_uid", yeongju.YEONGJU_EXPERIENCE_MENU_ID),
                ("reserve_uid", partition),
                ("initYear", year),
                ("initMonth", current_month),
                ("initDay", 1),
            )
        )
        active = ' class="on"' if current_month == month else ""
        month_links.append(
            f'<li{active}><a href="?{escape(query, quote=True)}">'
            f"{current_month}월</a></li>"
        )

    rows: list[str] = []
    for week in Calendar(firstweekday=6).monthdayscalendar(year, month):
        cells: list[str] = []
        for day_number in week:
            if day_number == 0:
                cells.append('<td><span class="date other_month"></span></td>')
                continue
            controls: list[str] = []
            for service_time, status, current, total in slots.get(day_number, []):
                if status == "OPEN":
                    assert current is not None and total is not None
                    controls.append(
                        _calendar_control(
                            partition,
                            f"{year:04d}-{month:02d}-{day_number:02d}",
                            service_time,
                            current,
                            total,
                            control_partition=control_partition,
                        )
                    )
                else:
                    controls.append(
                        f'<a href="#self"><span>{service_time} 마감</span></a>'
                    )
            if unknown_status and day_number == 7:
                controls.append('<a href="#self"><span>16:30 준비중</span></a>')
            if month == 8 and day_number == 15:
                controls.append('<a href="#self"><span>광복절</span></a>')
            cells.append(
                f'<td><span class="date">{day_number:02d}</span>'
                + "".join(controls)
                + "</td>"
            )
        rows.append("<tr>" + "".join(cells) + "</tr>")

    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>영주시 예약통합서비스</title></head>
<body><main id="container"><article class="article"><h3 class="hidden">실내놀이터</h3>
<div id="content" class="wrap"><div class="reserve_tab"><ul>{''.join(tab_items)}</ul></div>
<div class="reserList"><div class="monthTab"><div>
{year_link(year - 1, 'prev')}<p>{year}년</p>{year_link(year + 1, 'next')}
</div><ul>{''.join(month_links)}</ul></div><div class="cal"><p>{month:02d}월</p>
<table><caption>{month:02d}월 실내놀이터 달력 - 일, 월, 화, 수, 목, 금, 토 순으로 나타낸 표입니다.</caption>
<thead><tr>{''.join(f'<th scope="col">{day}</th>' for day in ('일', '월', '화', '수', '목', '금', '토'))}</tr></thead>
<tbody>{''.join(rows)}</tbody></table></div></div></div></article></main></body></html>"""


_FIXTURE_SLOTS: dict[tuple[str, int, int], dict[int, list[tuple[str, str, Optional[int], Optional[int]]]]] = {
    ("1", 2026, 8): {
        5: [("10:30", "OPEN", 0, 20)],
        6: [("10:30", "CLOSED", None, None)],
    },
    ("1", 2026, 9): {1: [("10:30", "OPEN", 2, 20)]},
    ("2", 2026, 8): {
        5: [("15:30", "OPEN", 1, 20)],
        6: [("15:30", "CLOSED", None, None)],
    },
    ("2", 2026, 9): {1: [("15:30", "OPEN", 0, 20)]},
}


def _months() -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    year, month = 2026, 8
    while (year, month) <= (2027, 12):
        result.append((year, month))
        month += 1
        if month == 13:
            year += 1
            month = 1
    return result


def _fixture(
    *,
    sentinel_slot: bool = False,
    control_partition: Optional[str] = None,
    unknown_status: bool = False,
) -> dict[str, str]:
    pages: dict[str, str] = {}
    for partition in yeongju.YEONGJU_EXPERIENCE_PARTITIONS:
        for year, month in _months():
            slots = _FIXTURE_SLOTS.get((partition.code, year, month), {})
            pages[
                yeongju.yeongju_experience_calendar_url(
                    year, month, partition
                )
            ] = _page(
                year,
                month,
                partition.code,
                slots,
                control_partition=(
                    control_partition
                    if partition.code == "1" and year == 2026 and month == 8
                    else None
                ),
                unknown_status=(
                    unknown_status
                    and partition.code == "1"
                    and year == 2026
                    and month == 8
                ),
            )
        sentinel_slots = (
            {5: [("10:30", "OPEN", 0, 20)]}
            if sentinel_slot and partition.code == "1"
            else {}
        )
        pages[
            yeongju.yeongju_experience_calendar_url(2028, 1, partition)
        ] = _page(2028, 1, partition.code, sentinel_slots)
    return pages


@dataclass
class _Response:
    html: str
    url: str
    status_code: int = 200

    def __post_init__(self) -> None:
        self.content = self.html.encode("utf-8")
        self.headers = {"content-type": "text/html;charset=utf-8"}
        self.history: tuple[Any, ...] = ()


class _Session:
    def close(self) -> None:
        return None


def _run(
    pages: Optional[Mapping[str, str]] = None,
    *,
    detail_limit: int = 20,
) -> tuple[tuple[list[dict[str, Any]], str, dict[str, Any]], list[str]]:
    pages = _fixture() if pages is None else pages
    calls: list[str] = []

    def fetcher(_session: Any, url: str, _timeout: int) -> _Response:
        calls.append(url)
        if url not in pages:
            raise AssertionError(f"unexpected request: {url}")
        return _Response(pages[url], url)

    result = yeongju.collect_yeongju_experience(
        _target(),
        today="2026-08-05",
        max_pages=24,
        detail_limit=detail_limit,
        session_factory=_Session,
        fetcher=fetcher,
    )
    return result, calls


def test_provider_candidate_target_and_calendar_get_allowlist_are_exact() -> None:
    normalized = normalized_duplicate_url(yeongju.YEONGJU_EXPERIENCE_URL)
    assert stable_provider(normalized) == yeongju.YEONGJU_EXPERIENCE_PROVIDER
    assert candidate_id(normalized) == yeongju.YEONGJU_EXPERIENCE_CANDIDATE_ID
    assert yeongju.is_yeongju_experience_target(_target())
    assert yeongju.is_yeongju_experience_target(
        _target(
            url=(
                "https://www.yeongju.go.kr/open_content/yeyak/page.do?"
                "code_uid=48&mnu_uid=11565"
            )
        )
    )
    assert not yeongju.is_yeongju_experience_target(_target(provider="OTHER"))
    assert not yeongju.is_yeongju_experience_target(
        _target(url=yeongju.YEONGJU_EXPERIENCE_URL + "&reserve_uid=1")
    )

    calendar = yeongju.yeongju_experience_calendar_url(2026, 8, "1")
    assert yeongju._request_kind("GET", calendar) == "calendar"
    for unsafe in (
        yeongju.YEONGJU_EXPERIENCE_URL + "&cmd=4&apply_date=2026-08-05",
        yeongju.YEONGJU_EXPERIENCE_URL + "&member_uid=1",
        yeongju.YEONGJU_EXPERIENCE_URL + "&day=2026-08-05",
        "https://www.yeongju.go.kr/login.do",
        "https://www.yeongju.go.kr/programs/board/download.do?file=1",
        calendar.replace("https://", "http://"),
        calendar + "#fragment",
    ):
        with pytest.raises(yeongju.YeongjuExperienceContractError):
            yeongju._request_kind("GET", unsafe)


def test_complete_two_partition_snapshot_and_no_application_request() -> None:
    (rows, parser, meta), calls = _run()
    assert parser == yeongju.YEONGJU_EXPERIENCE_PARSER
    assert [row["source_course_id"] for row in rows] == [
        "indoor-playground:1:2026-08-05:10:30",
        "indoor-playground:2:2026-08-05:15:30",
        "indoor-playground:1:2026-08-06:10:30",
        "indoor-playground:2:2026-08-06:15:30",
        "indoor-playground:1:2026-09-01:10:30",
        "indoor-playground:2:2026-09-01:15:30",
    ]
    assert {row["service_group"] for row in rows} == {"체험"}
    assert {row["domain_category"] for row in rows} == {"체험·견학"}
    assert {row["service_group_policy"] for row in rows} == {"locked"}
    assert {row["address"] for row in rows} == {
        yeongju.YEONGJU_EXPERIENCE_ADDRESS
    }
    assert rows[0]["capacity_current"] == 0
    assert rows[0]["capacity_total"] == 20
    assert rows[2]["status"] == "CLOSED"
    assert rows[2]["application_url"] == ""
    assert rows[0]["application_url"].startswith("https://www.yeongju.go.kr/")
    assert all("광복절" not in row["title"] for row in rows)

    assert meta["logical_requests"] == 42
    assert meta["calendar_requests"] == 42
    assert meta["calendar_months_per_partition"] == 17
    assert meta["calendar_partition_pages"] == 34
    assert meta["empty_sentinel_pages"] == 2
    assert meta["source_slot_count"] == 6
    assert meta["current_count"] == 6
    assert meta["status_counts"] == {"CLOSED": 2, "OPEN": 4}
    assert meta["partition_counts"] == {"1": 3, "2": 3}
    assert meta["month_counts"] == {"2026-08": 4, "2026-09": 2}
    assert meta["last_nonempty_month"] == {"1": "2026-09", "2": "2026-09"}
    assert meta["application_controls_observed_not_called"] == 4
    assert meta["application_endpoint_requests"] == 0
    assert meta["login_endpoint_requests"] == 0
    assert meta["pii_endpoint_requests"] == 0
    assert meta["pagination_complete"] is True
    assert meta["partitions_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    for url in calls:
        query = parse_qs(urlparse(url).query)
        assert "cmd" not in query
        assert "apply_date" not in query
        assert "apply_time" not in query
        assert "member_uid" not in query


def test_boundary_partition_and_status_drift_fail_atomically() -> None:
    (rows, _parser, meta), _ = _run(_fixture(sentinel_slot=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sentinel is not exact empty" in meta["errors"][0]

    (rows, _parser, meta), _ = _run(_fixture(control_partition="2"))
    assert rows == []
    assert "application control identity changed" in meta["errors"][0]

    (rows, _parser, meta), _ = _run(_fixture(unknown_status=True))
    assert rows == []
    assert "unknown time-bearing calendar status" in meta["errors"][0]


def test_boundary_recheck_and_detail_limit_are_atomic() -> None:
    pages = _fixture()
    current_url = yeongju.yeongju_experience_calendar_url(2026, 8, "1")
    changed = _page(
        2026,
        8,
        "1",
        {
            5: [("10:30", "OPEN", 3, 20)],
            6: [("10:30", "CLOSED", None, None)],
        },
    )
    calls: Counter[str] = Counter()

    def fetcher(_session: Any, url: str, _timeout: int) -> _Response:
        calls[url] += 1
        html = changed if url == current_url and calls[url] > 1 else pages[url]
        return _Response(html, url)

    rows, _parser, meta = yeongju.collect_yeongju_experience(
        _target(),
        today="2026-08-05",
        max_pages=24,
        detail_limit=20,
        session_factory=_Session,
        fetcher=fetcher,
    )
    assert rows == []
    assert "boundary changed during crawl" in meta["errors"][0]

    (rows, _parser, meta), _ = _run(detail_limit=5)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "detail_limit truncates" in meta["errors"][0]


def test_exact_empty_calendar_is_valid_and_does_not_invent_rows() -> None:
    pages = {
        url: _page(
            int(parse_qs(urlparse(url).query)["initYear"][0]),
            int(parse_qs(urlparse(url).query)["initMonth"][0]),
            parse_qs(urlparse(url).query)["reserve_uid"][0],
            {},
        )
        for url in _fixture()
    }
    (rows, _parser, meta), calls = _run(pages)
    assert rows == []
    assert meta["no_current_data"] is True
    assert meta["current_count"] == 0
    assert meta["snapshot_complete"] is True
    assert len(calls) == 40


def test_default_fetcher_keeps_tls_verification_and_redirects_disabled() -> None:
    captured: dict[str, Any] = {}

    class Session:
        def get(self, url: str, **kwargs: Any) -> object:
            captured["url"] = url
            captured.update(kwargs)
            return object()

    yeongju._default_fetcher(Session(), yeongju.YEONGJU_EXPERIENCE_URL, 7)
    assert captured["verify"] is True
    assert captured["allow_redirects"] is False
    assert captured["timeout"] == 7


def test_router_target_operational_and_coverage_are_wired(monkeypatch) -> None:
    from Crawler import Crawler_MunicipalYaml as municipal

    sentinel = ([{"id": 1}], yeongju.YEONGJU_EXPERIENCE_PARSER, {"snapshot_complete": True})
    captured: dict[str, Any] = {}

    def collect(*_args: Any, **kwargs: Any):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(yeongju, "collect_yeongju_experience", collect)
    target = municipal.CrawlTarget(
        provider=yeongju.YEONGJU_EXPERIENCE_PROVIDER,
        name="영주시 실내놀이터 체험·견학",
        branch=yeongju.YEONGJU_EXPERIENCE_BRANCH,
        url=yeongju.YEONGJU_EXPERIENCE_URL,
        source="test",
    )
    assert municipal.collect_from_url(
        target, timeout=3, max_pages=24, detail_limit=300
    ) == sentinel
    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])

    targets = yaml.safe_load(
        (ROOT / "config/crawl_targets/public_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )["targets"]
    matches = [
        item
        for item in targets
        if item.get("provider") == yeongju.YEONGJU_EXPERIENCE_PROVIDER
        and item.get("url") == yeongju.YEONGJU_EXPERIENCE_URL
    ]
    assert len(matches) == 1
    assert matches[0]["crawler_status"] == "ready"
    assert matches[0]["service_group"] == "체험"
    assert matches[0]["ops_scopes"] == ["experience"]
    assert matches[0]["last_quality"]["snapshot_complete"] is True

    operational = yaml.safe_load(
        (
            ROOT / "config/municipal_integrated_reservation_operational.yaml"
        ).read_text(encoding="utf-8")
    )["entries"]
    entries = [
        item
        for item in operational
        if item.get("provider") == yeongju.YEONGJU_EXPERIENCE_PROVIDER
        and item.get("target_url") == yeongju.YEONGJU_EXPERIENCE_URL
    ]
    assert len(entries) == 1
    assert entries[0]["validation_outcome"] == "collected"
    assert entries[0]["row_count"] == 111

    coverage = yaml.safe_load(
        (
            ROOT / "config/municipal_integrated_reservation_coverage.yaml"
        ).read_text(encoding="utf-8")
    )["municipalities"]
    municipality = next(
        item for item in coverage if item.get("code") == "4721000000"
    )
    for key in (
        "owner_providers",
        "promoted_providers",
        "yaml_owner_providers",
    ):
        assert municipality[key].count(yeongju.YEONGJU_EXPERIENCE_PROVIDER) == 1


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_YEONGJU_EXPERIENCE") != "1",
    reason="set RUN_LIVE_YEONGJU_EXPERIENCE=1 for the official live contract test",
)
def test_live_yeongju_indoor_playground_contract() -> None:
    rows, parser, meta = yeongju.collect_yeongju_experience(
        _target(),
        max_pages=24,
        detail_limit=300,
    )
    assert parser == yeongju.YEONGJU_EXPERIENCE_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["partition_codes"] == ["1", "2"]
    assert meta["application_endpoint_requests"] == 0
    assert meta["pii_endpoint_requests"] == 0
    assert all(row["service_group"] == "체험" for row in rows)
