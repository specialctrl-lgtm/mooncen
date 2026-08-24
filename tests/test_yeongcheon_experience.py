from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
import os
from typing import Any, Mapping

import pytest

from Crawler import municipal_yeongcheon_experience as collector


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


def _calendar_html(
    partition: collector.YeongcheonExperiencePartition,
    year: int,
    month: int,
    statuses: Mapping[int, str],
    *,
    unsafe_header: bool = False,
    bad_control_day: int | None = None,
) -> bytes:
    weeks = calendar.Calendar(firstweekday=6).monthdayscalendar(year, month)
    rows: list[str] = []
    for week in weeks:
        cells: list[str] = []
        for day in week:
            if not day:
                cells.append("<td></td>")
                continue
            label = statuses.get(day, "")
            body = str(day)
            if label in {
                collector._STATUS_OPEN,
                collector._STATUS_CLOSED,
                collector._STATUS_ENDED,
            }:
                control_day = bad_control_day if bad_control_day == day else day
                body += (
                    f" <a href=\"#\" onclick=\"viewTheDay('{year:04d}-{month:02d}-"
                    f"{control_day:02d}'); return false;\">{label}</a>"
                )
            elif label == collector._STATUS_HOLIDAY:
                body += f" <span class=\"red\">{label}</span>"
            cells.append(f"<td>{body}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")

    detail_headers = ["예약시간", "예약가능 여부", "관리"]
    if unsafe_header:
        detail_headers.insert(2, "예약자명")
    detail_cells = "".join("<td>-</td>" for _ in detail_headers)
    return f"""
<!doctype html>
<html><head><title>{partition.label} | 영천한의마을 | 체험/견학 | 영천 통합예약</title></head>
<body>
<form id="listForm" method="post" action="{partition.path}?mId={partition.menu_id}">
  <input name="year"><input name="month"><input name="date">
  <input name="rsvDate"><input name="rsvTime"><input name="selectedDate">
  <div class="calendar ycherb mT10">
    <div class="calendarHead">{year}.{month}</div>
    <table>
      <caption>{partition.label} 현황을 일, 월, 화, 수, 목, 금, 토 순으로 안내하는 표입니다.</caption>
      <thead><tr>{''.join(f'<th>{value}</th>' for value in collector._WEEKDAYS)}</tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </div>
  <table class="tbl ycherb taC">
    <caption>해당일의 신청정보</caption>
    <thead><tr>{''.join(f'<th>{value}</th>' for value in detail_headers)}</tr></thead>
    <tbody><tr>{detail_cells}</tr></tbody>
  </table>
</form>
</body></html>
""".encode("utf-8")


def _pages() -> dict[tuple[str, int, int], bytes]:
    footbath, herbal = collector.YEONGCHEON_EXPERIENCE_PARTITIONS
    return {
        (footbath.code, 2026, 8): _calendar_html(
            footbath,
            2026,
            8,
            {
                3: collector._STATUS_HOLIDAY,
                4: collector._STATUS_ENDED,
                5: collector._STATUS_CLOSED,
                6: collector._STATUS_OPEN,
            },
        ),
        (footbath.code, 2026, 9): _calendar_html(
            footbath, 2026, 9, {1: collector._STATUS_OPEN}
        ),
        (footbath.code, 2026, 10): _calendar_html(
            footbath, 2026, 10, {5: collector._STATUS_HOLIDAY}
        ),
        (herbal.code, 2026, 8): _calendar_html(
            herbal,
            2026,
            8,
            {
                3: collector._STATUS_ENDED,
                5: collector._STATUS_OPEN,
            },
        ),
        (herbal.code, 2026, 9): _calendar_html(
            herbal, 2026, 9, {2: collector._STATUS_CLOSED}
        ),
        (herbal.code, 2026, 10): _calendar_html(herbal, 2026, 10, {}),
    }


def _target() -> dict[str, str]:
    return {
        "provider": collector.YEONGCHEON_EXPERIENCE_PROVIDER,
        "url": collector.YEONGCHEON_EXPERIENCE_URL,
    }


def _fetcher_for(
    pages: Mapping[tuple[str, int, int], bytes],
    calls: list[tuple[str, int, int]],
):
    def fetcher(
        _session: Any,
        url: str,
        data: Mapping[str, str],
        _timeout: int,
    ) -> _Response:
        parsed, _ = collector._parse_url(url)
        partition = collector.YEONGCHEON_EXPERIENCE_PARTITION_BY_PATH[parsed.path]
        key = (partition.code, int(data["year"]), int(data["month"]))
        calls.append(key)
        return _Response(url=url, content=pages[key])

    return fetcher


def _collect(
    pages: Mapping[tuple[str, int, int], bytes] | None = None,
    *,
    detail_limit: int = 20,
    max_pages: int = 12,
    dedupe_rows=collector._dedupe_default,
):
    calls: list[tuple[str, int, int]] = []
    session = _Session()
    rows, parser, meta = collector.collect_yeongcheon_experience(
        _target(),
        today="2026-08-05",
        timeout=3,
        max_pages=max_pages,
        detail_limit=detail_limit,
        session_factory=lambda: session,
        fetcher=_fetcher_for(pages or _pages(), calls),
        dedupe_rows=dedupe_rows,
    )
    return rows, parser, meta, calls, session


def test_target_identity_is_exact() -> None:
    assert collector.is_yeongcheon_experience_target(_target())
    assert not collector.is_yeongcheon_experience_target(
        {**_target(), "provider": "MUNI_WWW_YC_GO_KR_54558363"}
    )
    assert not collector.is_yeongcheon_experience_target(
        {**_target(), "url": collector.YEONGCHEON_EXPERIENCE_URL + "&page=1"}
    )


def test_request_boundary_allows_only_two_month_list_posts() -> None:
    footbath = collector.YEONGCHEON_EXPERIENCE_PARTITIONS[0]
    assert (
        collector._request_partition(
            "POST", footbath.url, {"year": "2026", "month": "8"}
        )
        == footbath
    )
    with pytest.raises(collector.YeongcheonExperienceContractError):
        collector._request_partition(
            "GET", footbath.url, {"year": "2026", "month": "8"}
        )
    with pytest.raises(collector.YeongcheonExperienceContractError):
        collector._request_partition(
            "POST",
            "https://www.yc.go.kr/yeyak/oriental/footbath/apply.do?mId=0307020000",
            {"year": "2026", "month": "8"},
        )


def test_complete_two_partition_snapshot_and_taxonomy() -> None:
    rows, parser, meta, calls, session = _collect()

    assert parser == collector.YEONGCHEON_EXPERIENCE_PARSER
    assert len(rows) == 5
    assert meta["source_slot_count"] == 7
    assert meta["expired_slot_count"] == 2
    assert meta["current_count"] == 5
    assert meta["status_counts"] == {"CLOSED": 2, "OPEN": 3}
    assert meta["partition_counts"] == {"footbath": 3, "herbal": 2}
    assert meta["last_nonempty_month"] == {
        "footbath": "2026-09",
        "herbal": "2026-09",
    }
    assert meta["sentinel_month"] == {
        "footbath": "2026-10",
        "herbal": "2026-10",
    }
    assert meta["logical_requests"] == 12
    assert len(calls) == 12
    assert meta["application_endpoint_requests"] == 0
    assert meta["applicant_endpoint_requests"] == 0
    assert meta["attachment_endpoint_requests"] == 0
    assert meta["pii_endpoint_requests"] == 0
    assert meta["snapshot_complete"] is True
    assert session.closed is True

    assert len({row["provider_course_id"] for row in rows}) == len(rows)
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["domain_category"] == "체험·견학" for row in rows)
    assert all(row["program_type"] == "체험" for row in rows)
    assert all(row["municipality_code"] == "4723000000" for row in rows)
    assert all(row["application_url"] == "" for row in rows)
    assert all(row["reservation_available"] is False for row in rows)
    assert all(row["start_date"] >= date(2026, 8, 5) for row in rows)


def test_output_discards_phone_and_free_text() -> None:
    rows, _, meta, _, _ = _collect()
    assert meta["errors"] == []
    serialized = repr(rows)
    assert "054-330" not in serialized
    assert "예약자명" not in serialized
    assert "description" not in serialized
    assert "content" not in serialized


def test_unsafe_applicant_column_fails_closed() -> None:
    pages = _pages()
    footbath = collector.YEONGCHEON_EXPERIENCE_PARTITIONS[0]
    pages[(footbath.code, 2026, 8)] = _calendar_html(
        footbath,
        2026,
        8,
        {5: collector._STATUS_OPEN},
        unsafe_header=True,
    )
    rows, _, meta, _, session = _collect(pages)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "applicant/PII column" in meta["errors"][0]
    assert session.closed is True


def test_mismatched_day_identity_fails_closed() -> None:
    pages = _pages()
    footbath = collector.YEONGCHEON_EXPERIENCE_PARTITIONS[0]
    pages[(footbath.code, 2026, 8)] = _calendar_html(
        footbath,
        2026,
        8,
        {5: collector._STATUS_OPEN},
        bad_control_day=5,
    ).replace(b"2026-08-05'); return false", b"2026-08-06'); return false", 1)
    rows, _, meta, _, _ = _collect(pages)
    assert rows == []
    assert "reservation day identity changed" in meta["errors"][0]


def test_missing_sentinel_fails_closed() -> None:
    pages = _pages()
    for partition in collector.YEONGCHEON_EXPERIENCE_PARTITIONS:
        pages[(partition.code, 2026, 10)] = _calendar_html(
            partition, 2026, 10, {1: collector._STATUS_OPEN}
        )
    rows, _, meta, _, _ = _collect(pages, max_pages=3)
    assert rows == []
    assert "max_pages truncated" in meta["errors"][0]


def test_detail_limit_never_returns_a_partial_snapshot() -> None:
    rows, _, meta, _, _ = _collect(detail_limit=4)
    assert rows == []
    assert "detail_limit truncates" in meta["errors"][0]
    assert meta["snapshot_complete"] is False


def test_dedupe_change_fails_closed() -> None:
    rows, _, meta, _, _ = _collect(dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed complete output" in meta["errors"][0]


def test_network_failure_is_atomic_and_closes_session() -> None:
    session = _Session()

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise requests_error

    requests_error = RuntimeError("offline")
    rows, _, meta = collector.collect_yeongcheon_experience(
        _target(),
        today="2026-08-05",
        session_factory=lambda: session,
        fetcher=broken,
    )
    assert rows == []
    assert meta["error_kind"] == "network_or_parse"
    assert meta["snapshot_complete"] is False
    assert session.closed is True


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_CRAWLER_TESTS") != "1",
    reason="explicit live crawler test only",
)
def test_live_official_calendar_contract() -> None:
    rows, _, meta = collector.collect_yeongcheon_experience(
        _target(),
        today="2026-08-05",
        timeout=20,
        max_pages=12,
        detail_limit=500,
    )
    assert len(rows) == 98
    assert meta["source_slot_count"] == 104
    assert meta["status_counts"] == {"CLOSED": 4, "OPEN": 94}
    assert meta["partition_counts"] == {"footbath": 49, "herbal": 49}
    assert meta["sentinel_month"] == {
        "footbath": "2026-10",
        "herbal": "2026-10",
    }
    assert meta["application_endpoint_requests"] == 0
    assert meta["pii_endpoint_requests"] == 0
    assert meta["snapshot_complete"] is True
