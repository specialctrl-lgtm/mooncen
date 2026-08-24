from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as municipal


HEADERS = (
    "\uac15\uc88c\uba85",
    "\uc811\uc218/\ub300\uae30/\uc815\uc6d0",
    "\ud559\uc2b5\ub300\uc0c1",
    "\uc811\uc218\ubc29\ubc95",
    "\uc218\uac15\ub8cc",
    "\uc0c1\ud0dc",
)


def _response(text: str) -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response._content = text.encode("utf-8")
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.encoding = "utf-8"
    return response


def _list_row(
    seq: str,
    title: str,
    *,
    branch: str = "\ub2f9\ub9ac\ub3d9 \ub2f9\ub9ac\uc791\uc740\ub3c4\uc11c\uad00",
    apply_period: str = "2026-07-20 ~ 2026-08-10",
    period: str = "2026-08-14 ~ 2026-09-18",
    status: str = "\uc811\uc218\uc911",
) -> str:
    return f"""
    <tr>
      <td><a onclick="fn_view_page('{seq}')">
        <dl>
          <dt>{title}</dt>
          <dd>\ub3c4 \uc11c \uad00 : {branch}</dd>
          <dd>\uc811\uc218\uae30\uac04 : {apply_period}</dd>
          <dd>\ud559\uc2b5\uae30\uac04 : {period}</dd>
        </dl>
      </a></td>
      <td>3/1/10\uba85</td>
      <td>\ucd08\ub4f1\ud559\uc0dd</td>
      <td>\uc628\ub77c\uc778</td>
      <td>\ubb34\ub8cc</td>
      <td>{status}</td>
    </tr>
    """


def _page(
    *,
    page: int,
    total_pages: int,
    total_count: int,
    rows: list[str],
    token: str,
) -> str:
    headers = "".join(f"<th>{header}</th>" for header in HEADERS)
    return f"""
    <html><body>
      <form id="searchForm"><input name="_csrf" value="{token}"></form>
      <div class="board_edu_page">
        \ud398\uc774\uc9c0 : {page} / {total_pages} \uc804\uccb4\uac8c\uc2dc\ubb3c : {total_count}
      </div>
      <table><thead><tr>{headers}</tr></thead><tbody>{''.join(rows)}</tbody></table>
    </body></html>
    """


def _detail(
    title: str,
    *,
    branch: str = "\ub2f9\ub9ac\ub3d9 \ub2f9\ub9ac\uc791\uc740\ub3c4\uc11c\uad00",
    apply_period: str = "2026-07-20 ~ 2026-08-10",
    period: str = "2026-08-14 ~ 2026-09-18",
    schedule: str = "\uae08 | 16:00 ~ 18:00",
    status: str = "\uc811\uc218\uc911",
) -> str:
    return f"""
    <html><body>
      <table class="table_view"><tbody>
        <tr><th class="title">{title}
          <span class="fR"><span class="state">{status}</span></span>
        </th></tr>
        <tr>
          <th scope="row">\uc791\uc740\ub3c4\uc11c\uad00</th><td>{branch}</td>
          <th scope="row">\uc7ac\ub8cc\ube44</th><td>0 \uc6d0</td>
          <th scope="row">\uad50\uc7ac\ube44</th><td>0 \uc6d0</td>
        </tr>
        <tr>
          <th scope="row">\ud559\uc2b5\uae30\uac04</th><td>{period}</td>
          <th scope="row">\uc811\uc218\uae30\uac04</th><td>{apply_period}</td>
          <th scope="row">\uc811\uc218\ubc29\ubc95</th><td>\uc628\ub77c\uc778</td>
        </tr>
        <tr>
          <th scope="row">\uac15 \uc0ac \uba85</th><td>\uae40\uac15\uc0ac</td>
          <th scope="row">\uc218 \uac15 \ub8cc</th><td>\ubb34\ub8cc</td>
          <th scope="row">\uad50\uc721\ubc29\ubc95</th><td>\uc624\ud504\ub77c\uc778</td>
        </tr>
        <tr>
          <th scope="row">\uad50\uc721\ub300\uc0c1</th><td>\ucd08\ub4f1\ud559\uc0dd</td>
          <th scope="row">\uad50\uc721\uc8fc\uae30</th><td>{schedule}</td>
          <th scope="row">\uad50\uc721\uc815\uc6d0</th><td>10\uba85</td>
        </tr>
        <tr>
          <th scope="row">\ubb38\uc758\uc804\ud654</th><td>051-000-0000</td>
          <th scope="row">\uc900\ube44\ubb3c</th><td>\ud544\uae30\uad6c</td>
        </tr>
        <tr><th scope="row">\uc720\uc758\uc0ac\ud56d</th><td>\uac15\uc88c \uc548\ub0b4</td></tr>
      </tbody></table>
      <table class="tableSt_list"><tbody>
        <tr><td>\uc2e0\uccad\uc790</td><td>010-****-1234</td></tr>
      </tbody></table>
    </body></html>
    """


class FakeSession:
    def __init__(self, pages: dict[tuple[str, int], str]) -> None:
        self.pages = pages
        self.headers: dict[str, str] = {}
        self.last_token = "seed-token"
        self.posts: list[tuple[str, int]] = []
        self.closed = False

    def get(self, url: str, timeout: int) -> requests.Response:
        assert timeout == 7
        return _response(
            '<form id="searchForm"><input name="_csrf" value="seed-token"></form>'
        )

    def post(
        self,
        url: str,
        data: dict[str, str],
        timeout: int,
    ) -> requests.Response:
        assert timeout == 7
        assert data["_csrf"] == self.last_token
        key = (data["searchCourse"], int(data["page"]))
        self.posts.append(key)
        text = self.pages[key]
        soup = BeautifulSoup(text, "html.parser")
        self.last_token = soup.select_one("input[name='_csrf']").get("value", "")
        return _response(text)

    def close(self) -> None:
        self.closed = True


def _target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider="SAHA_SOCIAL_WELFARE_DIRECTORY",
        name="\uc0ac\ud558\uad6c \uc791\uc740\ub3c4\uc11c\uad00 \uac15\uc88c",
        branch="\uc0ac\ud558\uad6c \uc791\uc740\ub3c4\uc11c\uad00",
        url=(
            "https://health.saha.go.kr/reserve/lectureLib/list.do"
            "?mId=0500000000"
        ),
        source="test",
    )


def _detail_fetcher(
    details: dict[str, str],
    *,
    failures: dict[int, str] | None = None,
) -> Any:
    def fetch(
        urls: list[str],
        timeout: int,
    ) -> tuple[dict[int, BeautifulSoup], dict[int, str]]:
        assert timeout == 7
        soups: dict[int, BeautifulSoup] = {}
        for index, url in enumerate(urls):
            seq = parse_qs(urlparse(url).query)["seq"][0]
            if index not in (failures or {}):
                soups[index] = BeautifulSoup(details[seq], "html.parser")
        return soups, failures or {}

    return fetch


def _complete_pages() -> dict[tuple[str, int], str]:
    return {
        ("culture", 1): _page(
            page=1,
            total_pages=2,
            total_count=2,
            rows=[_list_row("101", "\uadf8\ub9bc\ucc45 \uad50\uc2e4")],
            token="culture-1",
        ),
        ("culture", 2): _page(
            page=2,
            total_pages=2,
            total_count=2,
            rows=[
                _list_row(
                    "102",
                    "\uc9c0\ub09c \uac15\uc88c",
                    apply_period="2026-06-01 ~ 2026-06-10",
                    period="2026-07-01 ~ 2026-07-27",
                    status="\uc885\uac15",
                )
            ],
            token="culture-2",
        ),
        ("deulak", 1): _page(
            page=1,
            total_pages=1,
            total_count=1,
            rows=[
                _list_row(
                    "201",
                    "\ub4e4\ub77d\ub0a0\ub77d \uacf5\uc608",
                    branch=(
                        "\uad34\uc815\ub3d9 \uaf4c\uce58\ub9c8\uc744 "
                        "\uc5b4\ub9b0\uc774\uc791\uc740\ub3c4\uc11c\uad00"
                    ),
                    status="\uc811\uc218\uc644\ub8cc",
                )
            ],
            token="deulak-1",
        ),
    }


def test_collects_complete_tabs_and_current_details_only() -> None:
    fake = FakeSession(_complete_pages())
    details = {
        "101": _detail("\uadf8\ub9bc\ucc45 \uad50\uc2e4"),
        "201": _detail(
            "\ub4e4\ub77d\ub0a0\ub77d \uacf5\uc608",
            branch="\uad34\uc815\ub3d9 \uaf4c\uce58\ub9c8\uc744\uc791\uc740\ub3c4\uc11c\uad00",
            status="\uc811\uc218\uc644\ub8cc",
        ),
    }

    rows, parser, meta = municipal.collect_saha_library_lectures(
        _target(),
        timeout=7,
        max_pages=3,
        detail_limit=2,
        session_factory=lambda: fake,
        today=date(2026, 7, 28),
        detail_fetcher=_detail_fetcher(details),
    )

    assert parser == "saha_library_two_tabs_complete+current_detail"
    assert fake.posts == [("culture", 1), ("deulak", 1), ("culture", 2)]
    assert fake.closed is True
    assert len(rows) == 2
    first = rows[0]
    assert first["target"] == "\ucd08\ub4f1\ud559\uc0dd"
    assert first["fee"] == "\ubb34\ub8cc"
    assert first["period"] == "2026-08-14 ~ 2026-09-18"
    assert first["venue_name"] == "\ub2f9\ub9ac\ub3d9 \ub2f9\ub9ac\uc791\uc740\ub3c4\uc11c\uad00"
    assert first["category"] == "\ub3c4\uc11c\uad00 \ud504\ub85c\uadf8\ub7a8/\ub3c5\uc11c\ubb38\ud654"
    assert first["schedule_raw"] == "\uae08 | 16:00 ~ 18:00"
    assert first["application_url"].endswith(
        "/write.do?mId=0500000000&seq=101"
    )
    assert "010-****-1234" not in str(first["raw_fields"])
    assert rows[1].get("application_url") is None
    assert meta["pages"] == 3
    assert meta["detail_pages"] == 2
    assert meta["discovered_links"] == 3
    assert meta["expired_count"] == 1
    assert meta["branch_alias_count"] == 1
    assert meta["pagination_complete"] is True


def test_complete_catalogue_must_fit_page_cap_before_remaining_pages() -> None:
    pages = {
        ("culture", 1): _page(
            page=1,
            total_pages=3,
            total_count=3,
            rows=[_list_row("101", "\uadf8\ub9bc\ucc45 \uad50\uc2e4")],
            token="culture-1",
        ),
        ("deulak", 1): _page(
            page=1,
            total_pages=2,
            total_count=2,
            rows=[_list_row("201", "\uacf5\uc608 \uad50\uc2e4")],
            token="deulak-1",
        ),
    }
    fake = FakeSession(pages)

    with pytest.raises(RuntimeError, match="requires 5 pages; max_pages=4"):
        municipal.collect_saha_library_lectures(
            _target(),
            timeout=7,
            max_pages=4,
            detail_limit=10,
            session_factory=lambda: fake,
            today=date(2026, 7, 28),
        )
    assert fake.posts == [("culture", 1), ("deulak", 1)]


def test_declared_list_count_mismatch_fails_closed() -> None:
    pages = {
        ("culture", 1): _page(
            page=1,
            total_pages=1,
            total_count=2,
            rows=[_list_row("101", "\uadf8\ub9bc\ucc45 \uad50\uc2e4")],
            token="culture-1",
        ),
        ("deulak", 1): _page(
            page=1,
            total_pages=1,
            total_count=0,
            rows=[],
            token="deulak-1",
        ),
    }

    with pytest.raises(RuntimeError, match="parsed 1 of 2 declared rows"):
        municipal.collect_saha_library_lectures(
            _target(),
            timeout=7,
            max_pages=2,
            detail_limit=10,
            session_factory=lambda: FakeSession(pages),
            today=date(2026, 7, 28),
        )


def test_any_current_detail_fetch_failure_fails_closed() -> None:
    fake = FakeSession(_complete_pages())
    details = {
        "101": _detail("\uadf8\ub9bc\ucc45 \uad50\uc2e4"),
        "201": _detail(
            "\ub4e4\ub77d\ub0a0\ub77d \uacf5\uc608",
            branch="\uad34\uc815\ub3d9 \uaf4c\uce58\ub9c8\uc744\uc791\uc740\ub3c4\uc11c\uad00",
        ),
    }

    with pytest.raises(RuntimeError, match="detail fetch failed for 1 courses"):
        municipal.collect_saha_library_lectures(
            _target(),
            timeout=7,
            max_pages=3,
            detail_limit=2,
            session_factory=lambda: fake,
            today=date(2026, 7, 28),
            detail_fetcher=_detail_fetcher(details, failures={1: "Timeout"}),
        )


def test_missing_required_detail_schedule_fails_closed() -> None:
    pages = {
        ("culture", 1): _page(
            page=1,
            total_pages=1,
            total_count=1,
            rows=[_list_row("101", "\uadf8\ub9bc\ucc45 \uad50\uc2e4")],
            token="culture-1",
        ),
        ("deulak", 1): _page(
            page=1,
            total_pages=1,
            total_count=0,
            rows=[],
            token="deulak-1",
        ),
    }

    with pytest.raises(RuntimeError, match="missing fields"):
        municipal.collect_saha_library_lectures(
            _target(),
            timeout=7,
            max_pages=2,
            detail_limit=1,
            session_factory=lambda: FakeSession(pages),
            today=date(2026, 7, 28),
            detail_fetcher=_detail_fetcher(
                {"101": _detail("\uadf8\ub9bc\ucc45 \uad50\uc2e4", schedule="")}
            ),
        )
