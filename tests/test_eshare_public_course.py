from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
import requests
from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as municipal


def _response(*, text: str = "", payload: Any = None) -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    if payload is not None:
        response._content = json.dumps(payload).encode("utf-8")
        response.headers["Content-Type"] = "application/json"
    else:
        response._content = text.encode("utf-8")
        response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.encoding = "utf-8"
    return response


def _card(
    rsrc_no: str,
    title: str,
    *,
    reservation_type: str,
    fee: str = "10,000\uc6d0",
) -> str:
    return f"""
    <li class="item">
      <div class="detail" data-rsrcno="{rsrc_no}" data-linkurl="">
        <div class="rsrcNmWrap"><strong>{title}</strong></div>
        <ul class="infoList">
          <li><strong>\uc608\uc57d\ubc29\ubc95</strong> \uc2e4\uc2dc\uac04\uc608\uc57d</li>
          <li><strong>\uc774\uc6a9\uc694\uae08</strong> {fee}</li>
        </ul>
      </div>
      <div class="category">
        <span>\uc11c\uc6b8</span><span>\uc911\uad6c</span><span>{reservation_type}</span>
      </div>
    </li>
    """


def _list_page(cards: list[str], *, total: int) -> str:
    return (
        f'<input id="tot_rows_cnt" value="{total}">'
        f'<input id="tot_page_size" value="0">'
        f"<ul>{''.join(cards)}</ul>"
    )


def _detail(title: str, *, target: str = "") -> str:
    return f"""
    <html><body><table><tbody>
      <tr>
        <th>\uc790\uc6d0 \ubd84\ub958</th>
        <td data-cell-header="\uc790\uc6d0 \ubd84\ub958">\uad50\uc721\u00b7\uac15\uc88c &gt; \ubbf8\uc220</td>
        <th>\uc790\uc6d0 \uba85\uce6d</th>
        <td data-cell-header="\uc790\uc6d0 \uba85\uce6d">{title}</td>
      </tr>
      <tr>
        <th>\uc7a5\uc18c/\uc704\uce58</th>
        <td data-cell-header="\uc7a5\uc18c/\uc704\uce58">\uc11c\uc6b8 \uc911\uad6c \ud14c\uc2a4\ud2b8\ub85c 1 \uc9c0\ub3c4\ubcf4\uae30</td>
        <th>\uc81c\uacf5 \uae30\uad00</th>
        <td data-cell-header="\uc81c\uacf5 \uae30\uad00">\uc911\uad6c \ubb38\ud654\uc13c\ud130</td>
      </tr>
      <tr>
        <th>\uc608\uc57d \ubb38\uc758</th>
        <td data-cell-header="\uc608\uc57d \ubb38\uc758">02-000-0000</td>
        <th>\uc774\uc6a9 \ub300\uc0c1</th>
        <td data-cell-header="\uc774\uc6a9 \ub300\uc0c1">{target}</td>
      </tr>
      <tr>
        <th>\uc774\uc6a9 \uc694\uae08</th>
        <td data-cell-header="\uc774\uc6a9 \uc694\uae08">\uae30\ubcf8\uc694\uae08 10,000\uc6d0</td>
        <th>\uc608\uc57d \ubc29\ubc95</th>
        <td data-cell-header="\uc608\uc57d \ubc29\ubc95">\uc628\ub77c\uc778 \uc9c1\uc811 \uc608\uc57d</td>
      </tr>
    </tbody></table></body></html>
    """


def _calendar(*dates: str) -> list[dict[str, Any]]:
    return [
        {
            "date": value,
            "useYn": "Y",
            "exclShareYn": "N",
            "magamcheck": "N",
            "excelShareMsg": "",
            "drawYn": "N",
        }
        for value in dates
    ]


def _slot(
    *,
    start: str,
    end: str,
    capacity: int = 10,
    current: int = 2,
) -> dict[str, Any]:
    return {
        "usePsblBgnTm": start,
        "usePsblEndTm": end,
        "useCapa": capacity,
        "useCapaQty": current,
        "dtlSeq": 1,
    }


class FakeSession:
    def __init__(
        self,
        *,
        list_html: str,
        details: dict[str, str],
        calendars: dict[str, list[dict[str, Any]]],
        schedules: dict[tuple[str, str], list[dict[str, Any]]],
    ) -> None:
        self.headers: dict[str, str] = {}
        self.list_html = list_html
        self.details = details
        self.calendars = calendars
        self.schedules = schedules
        self.posts: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, timeout: int) -> requests.Response:
        assert timeout == 7
        rsrc_no = url.split("rsrc_no=", 1)[1]
        return _response(text=self.details[rsrc_no])

    def post(
        self,
        url: str,
        data: dict[str, str],
        timeout: int,
    ) -> requests.Response:
        assert timeout == 7
        self.posts.append((url, dict(data)))
        if url == municipal.ESHARE_LIST_URL:
            assert data["searchIntnetRsrvPsblYn"] == "Y"
            assert data["rows_per_page"] == str(municipal.ESHARE_LIST_PAGE_SIZE)
            return _response(text=self.list_html)
        rsrc_no = url.split("/Upv/", 1)[1].split("/", 1)[0]
        if url.endswith("selectCpsResvExclShareCalendar.do"):
            return _response(payload=self.calendars[rsrc_no])
        if url.endswith("searchCpsGnrResvSchedDetlQty.do"):
            return _response(payload=self.schedules[(rsrc_no, data["searchDate"])])
        raise AssertionError(url)


def _target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider="ESHARE_PUBLIC_COURSE",
        name="\uacf5\uc720\ub204\ub9ac \uad50\uc721/\uac15\uc88c",
        branch="\uacf5\uc720\ub204\ub9ac",
        url=municipal.ESHARE_SEARCH_URL,
        source="test",
        extra={
            "collection_category": "\uacf5\uacf5\uc608\uc57d",
            "domain_category": "\uad50\uc721\u00b7\uac15\uc88c",
        },
    )


def test_eshare_pairs_preserve_empty_semantic_cells() -> None:
    soup = BeautifulSoup(
        """
        <table><tr>
          <th>\uc608\uc57d \ubb38\uc758</th><td data-cell-header="\uc608\uc57d \ubb38\uc758">02-1</td>
          <th>\uc774\uc6a9 \ub300\uc0c1</th><td data-cell-header="\uc774\uc6a9 \ub300\uc0c1"></td>
        </tr></table>
        """,
        "html.parser",
    )

    assert municipal.eshare_pairs_from_tables(soup) == {
        "\uc608\uc57d \ubb38\uc758": "02-1",
        "\uc774\uc6a9 \ub300\uc0c1": "",
    }


def test_collects_only_complete_realtime_calendar_courses() -> None:
    realtime = municipal.ESHARE_REALTIME_RESERVATION
    list_html = _list_page(
        [
            _card("COURSE1", "\ubbf8\uc220 \uad50\uc2e4", reservation_type=realtime),
            _card("STALE01", "\uc9c0\ub09c \uac15\uc88c", reservation_type=realtime),
            _card("EXTERNAL1", "\uc678\ubd80 \uac15\uc88c", reservation_type="\uc678\ubd80\uc608\uc57d"),
        ],
        total=3,
    )
    fake = FakeSession(
        list_html=list_html,
        details={
            "COURSE1": _detail("\ubbf8\uc220 \uad50\uc2e4"),
            "STALE01": _detail("\uc9c0\ub09c \uac15\uc88c", target="\uc131\uc778"),
        },
        calendars={
            "COURSE1": _calendar("2026-07-30", "2026-08-06"),
            "STALE01": [],
        },
        schedules={
            ("COURSE1", "2026-07-30"): [
                _slot(start="1000", end="1100"),
                _slot(start="1300", end="1400", capacity=5, current=5),
            ],
        },
    )

    rows, parser, meta = municipal.collect_eshare_public_courses(
        _target(),
        timeout=7,
        max_pages=2,
        detail_limit=2,
        session_factory=lambda: fake,
        today=date(2026, 7, 28),
    )

    assert parser == "eshare_realtime_calendar"
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "\ubbf8\uc220 \uad50\uc2e4"
    assert row["target"] == municipal.ESHARE_TARGET_FALLBACK
    assert row["fee"] == "\uae30\ubcf8\uc694\uae08 10,000\uc6d0"
    assert row["period"] == "2026-07-30 ~ 2026-08-06 (\uc608\uc57d \uac00\ub2a5 2\uc77c)"
    assert row["venue_address"] == "\uc11c\uc6b8 \uc911\uad6c \ud14c\uc2a4\ud2b8\ub85c 1"
    assert row["category"] == "\uad50\uc721\u00b7\uac15\uc88c > \ubbf8\uc220"
    assert row["schedule_raw"] == "10:00~11:00"
    assert row["status"] == "\uc608\uc57d\uac00\ub2a5"
    assert row["raw_fields"]["available_dates"] == ["2026-07-30", "2026-08-06"]
    assert meta["discovered_links"] == 3
    assert meta["realtime_rows"] == 2
    assert meta["excluded_external_rows"] == 1
    assert meta["no_available_dates"] == 1
    assert meta["calendar_requests"] == 2
    assert meta["schedule_requests"] == 1


def test_realtime_scope_must_fit_detail_limit() -> None:
    realtime = municipal.ESHARE_REALTIME_RESERVATION
    fake = FakeSession(
        list_html=_list_page(
            [
                _card("COURSE1", "\uac15\uc88c 1", reservation_type=realtime),
                _card("COURSE2", "\uac15\uc88c 2", reservation_type=realtime),
            ],
            total=2,
        ),
        details={},
        calendars={},
        schedules={},
    )

    with pytest.raises(RuntimeError, match="detail scope 2 exceeds detail_limit=1"):
        municipal.collect_eshare_public_courses(
            _target(),
            timeout=7,
            max_pages=1,
            detail_limit=1,
            session_factory=lambda: fake,
            today=date(2026, 7, 28),
        )


def test_incomplete_pagination_fails_closed() -> None:
    fake = FakeSession(
        list_html=_list_page(
            [
                _card(
                    "COURSE1",
                    "\uac15\uc88c 1",
                    reservation_type=municipal.ESHARE_REALTIME_RESERVATION,
                )
            ],
            total=2,
        ),
        details={},
        calendars={},
        schedules={},
    )

    with pytest.raises(RuntimeError, match="pagination incomplete"):
        municipal.collect_eshare_public_courses(
            _target(),
            timeout=7,
            max_pages=1,
            detail_limit=10,
            session_factory=lambda: fake,
            today=date(2026, 7, 28),
        )


def test_calendar_and_schedule_filters_match_open_slots() -> None:
    calendar_rows = _calendar("2026-07-27", "2026-07-28", "2026-07-29")
    calendar_rows[1]["magamcheck"] = "Y"
    calendar_rows[2]["drawYn"] = "Y"
    calendar_rows[2]["applsdtm"] = "2026-07-01 00:00"
    calendar_rows[2]["appledtm"] = "2026-07-31 23:59"

    assert [
        item["date"]
        for item in municipal.eshare_open_calendar_entries(
            calendar_rows,
            today=date(2026, 7, 28),
        )
    ] == ["2026-07-29"]
    assert municipal.eshare_schedule_text(
        municipal.eshare_open_schedule_slots(
            [
                _slot(start="0900", end="1000", capacity=5, current=5),
                _slot(start="1030", end="1200", capacity=5, current=1),
            ]
        )
    ) == "10:30~12:00"
