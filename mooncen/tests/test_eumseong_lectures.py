from __future__ import annotations

import dotenv
import pytest
from bs4 import BeautifulSoup

dotenv.load_dotenv = lambda *args, **kwargs: False

from Crawler import Crawler_MunicipalYaml as municipal  # noqa: E402


LIST_PAGE = """
<html><body>
  <div class="bbs_page">\ucd1d\uac8c\uc2dc\ubb3c : 1 \uac74 \ud398\uc774\uc9c0 : 1 / 1</div>
  <table class="p-table simple">
    <thead><tr>
      <th>\ubc88\ud638</th><th>\ub300\uc0c1</th><th>\uac15\uc88c\uba85</th><th>\ud559\uc2b5\uc7a5\uc18c</th>
      <th>\uc811\uc218\uae30\uac04</th><th>\uac15\uc758\uae30\uac04</th><th>\uc0c1\ud0dc</th><th>\uc218\uac15\uc2e0\uccad</th>
    </tr></thead>
    <tbody>
      <tr>
        <td>1</td><td>\uccad\uc18c\ub144</td>
        <td><a href="./selectEduLctreWebView.do?lctreNo=832&amp;pageIndex=1&amp;key=61">\ud765\ubbf8\uc640 \uc801\uc131\uc73c\ub85c \ucc3e\ub294 \ub098\uc758 \uc9c4\ub85c</a></td>
        <td>\ucda9\ubd81\ud601\uc2e0\ub3c4\uc2dc \uacf5\uc720\ud3c9\uc0dd\ud559\uc2b5\uad00</td>
        <td><time>2099-07-06</time> ~ <time>2099-07-19</time></td>
        <td><time>2099-07-27</time> ~ <time>2099-07-27</time></td>
        <td><span>\uc811\uc218\uc911</span><span>\uc778\uc6d0: <em>1</em>/<em>10</em></span></td>
        <td><a href="./addEduLctreApplcntWebView.do?lctreNo=832&amp;key=61">\uc218\uac15\uc2e0\uccad</a></td>
      </tr>
    </tbody>
  </table>
</body></html>
"""


SENTINEL_PAGE = """
<html><body>
  <div class="bbs_page">\ucd1d\uac8c\uc2dc\ubb3c : 1 \uac74 \ud398\uc774\uc9c0 : 2 / 1</div>
  <p class="no-data">\ub4f1\ub85d\ub41c \uac15\uc88c\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.</p>
</body></html>
"""


DETAIL_PAGE = """
<html><body>
  <table class="table type2 left"><tbody>
    <tr><th>\uc0ac\uc5c5\uba85</th><td>2099\ub144 \uad50\uc721\ubc1c\uc804\ud2b9\uad6c</td></tr>
    <tr><th>\uac15\uc88c\uba85</th><td>\ud765\ubbf8\uc640 \uc801\uc131\uc73c\ub85c \ucc3e\ub294 \ub098\uc758 \uc9c4\ub85c</td></tr>
    <tr><th>\ubd84\uc57c</th><td>\uc778\ubb38\uad50\uc591\uad50\uc721</td></tr>
    <tr><th>\ub300\uc0c1</th><td>\uccad\uc18c\ub144</td></tr>
    <tr><th>\uc811\uc218\uae30\uac04</th><td>2099-07-06 ~ 2099-07-19</td></tr>
    <tr><th>\uac15\uc758\uae30\uac04</th><td>2099\ub144 07\uc6d4 27\uc77c ~ 2099\ub144 07\uc6d4 27\uc77c</td></tr>
    <tr><th>\uac15\uc758\uc2dc\uac04</th><td>10:00~13:00</td></tr>
    <tr><th>\uc218\uac15\ub8cc</th><td>\ubb34\ub8cc 0\uc6d0</td></tr>
    <tr><th>\ubaa8\uc9d1\uc778\uc6d0</th><td>\ucd1d 10\uba85</td></tr>
    <tr><th>\ud559\uc2b5\uc7a5\uc18c</th><td>\ucda9\ubd81\ud601\uc2e0\ub3c4\uc2dc \uacf5\uc720\ud3c9\uc0dd\ud559\uc2b5\uad00</td></tr>
    <tr><th>\uc218\uac15\uc2e0\uccad \uc720\uc758\uc0ac\ud56d</th><td>\uc911\ud559\uc0dd \uc774\uc0c1 \uccad\uc18c\ub144\ub9cc \uc2e0\uccad</td></tr>
  </tbody></table>
</body></html>
"""


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _target(
    *,
    provider: str = municipal.EUMSEONG_LECTURE_PROVIDER,
    url: str = municipal.EUMSEONG_LECTURE_CANONICAL_URL,
) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=provider,
        name="Eumseong",
        branch="Eumseong-gun",
        url=url,
        source="test",
        priority=1,
        extra={},
    )


def _detail_url(lecture_no: str = "832") -> str:
    return (
        "https://edu.eumseong.go.kr/www/selectEduLctreWebView.do?"
        f"key=61&lctreNo={lecture_no}"
    )


def _collect(
    monkeypatch: pytest.MonkeyPatch,
    pages: dict[str, str | Exception],
    *,
    target: municipal.CrawlTarget | None = None,
    max_pages: int = 5,
    detail_limit: int = 5,
):
    calls: list[str] = []

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        calls.append(url)
        value = pages[url]
        if isinstance(value, Exception):
            raise value
        return _soup(value)

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
    rows, parser, meta = municipal.collect_eumseong_lectures(
        target or _target(),
        timeout=7,
        max_pages=max_pages,
        detail_limit=detail_limit,
    )
    return rows, parser, meta, calls


def _complete_pages(detail: str | Exception = DETAIL_PAGE) -> dict[str, str | Exception]:
    return {
        municipal.eumseong_lecture_list_url(1): LIST_PAGE,
        municipal.eumseong_lecture_list_url(2): SENTINEL_PAGE,
        _detail_url(): detail,
    }


def test_eumseong_complete_snapshot_uses_stable_identity_and_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, parser, meta, calls = _collect(monkeypatch, _complete_pages())

    assert parser == municipal.EUMSEONG_LECTURE_PARSER
    assert len(rows) == 1
    assert rows[0]["title"] == "\ud765\ubbf8\uc640 \uc801\uc131\uc73c\ub85c \ucc3e\ub294 \ub098\uc758 \uc9c4\ub85c"
    assert rows[0]["title"] != "2099\ub144 \uad50\uc721\ubc1c\uc804\ud2b9\uad6c"
    assert rows[0]["provider_course_id"].endswith(":lctre:832")
    assert rows[0]["raw_url"] == _detail_url()
    assert rows[0]["application_url"] == (
        "https://edu.eumseong.go.kr/www/addEduLctreApplcntWebView.do?"
        "key=61&lctreNo=832"
    )
    assert rows[0]["reservation_available"] is True
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["branch"] == (
        "\ucda9\ubd81\ud601\uc2e0\ub3c4\uc2dc \uacf5\uc720\ud3c9\uc0dd\ud559\uc2b5\uad00"
    )
    assert rows[0]["municipality_code"] == "4377000000"
    assert rows[0]["capacity_current"] == 1
    assert rows[0]["capacity_total"] == 10
    assert meta["pages"] == 2
    assert meta["list_requests"] == 2
    assert meta["physical_requests"] == 3
    assert meta["source_total"] == 1
    assert meta["source_rows"] == 1
    assert meta["sentinel_pages"] == 1
    assert meta["detail_pages"] == 1
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert calls == [
        municipal.eumseong_lecture_list_url(1),
        municipal.eumseong_lecture_list_url(2),
        _detail_url(),
    ]


def test_eumseong_scheduled_status_uses_official_status_badge() -> None:
    soup = _soup(LIST_PAGE)
    badge = soup.select_one("tbody td:nth-of-type(7) span")
    assert badge is not None
    badge["class"] = ["status", "n1"]
    badge.string = "\uc608\uc815"
    application_cell = soup.select_one("tbody td:nth-of-type(8)")
    assert application_cell is not None
    application_cell.clear()

    rows, errors = municipal.parse_eumseong_lecture_rows(
        _target(),
        soup,
        municipal.eumseong_lecture_list_url(1),
        1,
    )

    assert errors == []
    assert len(rows) == 1
    assert rows[0]["status"] == "SCHEDULED"
    assert rows[0]["reservation_available"] is False
    assert rows[0]["raw_fields"]["source_status"] == "\uc608\uc815"


def test_eumseong_canonical_url_drops_page_identity() -> None:
    first, first_no = municipal.canonical_eumseong_lecture_url(
        "https://edu.eumseong.go.kr/www/selectEduLctreWebList.do?key=61&pageIndex=1",
        "./selectEduLctreWebView.do?lctreNo=832&pageIndex=1&key=61",
    )
    second, second_no = municipal.canonical_eumseong_lecture_url(
        "https://edu.eumseong.go.kr/www/selectEduLctreWebList.do?key=61&pageIndex=2",
        "./selectEduLctreWebView.do?lctreNo=832&pageIndex=2&key=61",
    )

    assert first_no == second_no == "832"
    assert first == second == "https://edu.eumseong.go.kr/www/selectEduLctreWebView.do?key=61&lctreNo=832"

    application = municipal.canonical_eumseong_application_url(
        municipal.eumseong_lecture_list_url(1),
        "./addEduLctreApplcntWebView.do?lctreNo=832&pageIndex=1&key=61",
        "832",
    )
    assert application == (
        "https://edu.eumseong.go.kr/www/addEduLctreApplcntWebView.do?"
        "key=61&lctreNo=832"
    )


def test_eumseong_max_page_and_detail_caps_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, _parser, meta, calls = _collect(
        monkeypatch,
        {municipal.eumseong_lecture_list_url(1): LIST_PAGE},
        max_pages=1,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]
    assert calls == [municipal.eumseong_lecture_list_url(1)]

    rows, _parser, meta, calls = _collect(
        monkeypatch,
        {
            municipal.eumseong_lecture_list_url(1): LIST_PAGE,
            municipal.eumseong_lecture_list_url(2): SENTINEL_PAGE,
        },
        detail_limit=0,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap allows 0 of 1" in meta[
        "configured_collection_error"
    ]
    assert _detail_url() not in calls


def test_eumseong_nonempty_sentinel_and_detail_failure_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nonempty_sentinel = LIST_PAGE.replace("1 / 1", "2 / 1", 1)
    rows, _parser, meta, calls = _collect(
        monkeypatch,
        {
            municipal.eumseong_lecture_list_url(1): LIST_PAGE,
            municipal.eumseong_lecture_list_url(2): nonempty_sentinel,
        },
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sentinel page is not empty" in meta["configured_collection_error"]
    assert _detail_url() not in calls

    rows, _parser, meta, _calls = _collect(
        monkeypatch,
        _complete_pages(RuntimeError("detail unavailable")),
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["detail_errors"] == 1
    assert "detail fetch RuntimeError" in meta["configured_collection_error"]


def test_eumseong_detail_branch_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mismatched = DETAIL_PAGE.replace(
        "\ucda9\ubd81\ud601\uc2e0\ub3c4\uc2dc \uacf5\uc720\ud3c9\uc0dd\ud559\uc2b5\uad00",
        "\uc74c\uc131\uad70 \uae08\ube5b\ud3c9\uc0dd\ud559\uc2b5\uad00",
    )
    rows, _parser, meta, _calls = _collect(
        monkeypatch,
        _complete_pages(mismatched),
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "list/detail venue mismatch" in meta[
        "configured_collection_error"
    ]


def test_eumseong_complete_expired_snapshot_reports_no_current_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expired = LIST_PAGE.replace("2099-", "2000-")
    rows, _parser, meta, calls = _collect(
        monkeypatch,
        {
            municipal.eumseong_lecture_list_url(1): expired,
            municipal.eumseong_lecture_list_url(2): SENTINEL_PAGE,
        },
    )
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["current_count"] == 0
    assert meta["expired_count"] == 1
    assert _detail_url() not in calls


def test_eumseong_explicit_jincheon_branch_is_not_owned_by_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external = LIST_PAGE.replace(
        "\ucda9\ubd81\ud601\uc2e0\ub3c4\uc2dc \uacf5\uc720\ud3c9\uc0dd\ud559\uc2b5\uad00",
        "\uc9c4\ucc9c\uad70\ud3c9\uc0dd\ud559\uc2b5\uad00",
    )
    rows, _parser, meta, calls = _collect(
        monkeypatch,
        {
            municipal.eumseong_lecture_list_url(1): external,
            municipal.eumseong_lecture_list_url(2): SENTINEL_PAGE,
        },
    )

    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["external_owner_count"] == 1
    assert meta["external_owner_identities"] == ["832"]
    assert meta["current_count"] == 0
    assert meta["no_current_data"] is True
    assert _detail_url() not in calls


def test_eumseong_duplicate_lecture_identity_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tbody = LIST_PAGE.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    second_row = tbody.replace("<td>1</td>", "<td>2</td>", 1)
    duplicate_page = LIST_PAGE.replace(
        "\ucd1d\uac8c\uc2dc\ubb3c : 1 \uac74",
        "\ucd1d\uac8c\uc2dc\ubb3c : 2 \uac74",
        1,
    ).replace(tbody, second_row + tbody, 1)
    sentinel = SENTINEL_PAGE.replace(
        "\ucd1d\uac8c\uc2dc\ubb3c : 1 \uac74",
        "\ucd1d\uac8c\uc2dc\ubb3c : 2 \uac74",
        1,
    )

    rows, _parser, meta, calls = _collect(
        monkeypatch,
        {
            municipal.eumseong_lecture_list_url(1): duplicate_page,
            municipal.eumseong_lecture_list_url(2): sentinel,
        },
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["duplicate_count"] == 1
    assert "duplicate lecture identities" in meta[
        "configured_collection_error"
    ]
    assert _detail_url() not in calls


def test_eumseong_wrong_provider_or_route_is_rejected_without_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        municipal,
        "session",
        lambda: pytest.fail("invalid target must not create a session"),
    )
    rows, parser, meta = municipal.collect_eumseong_lectures(
        _target(provider="MUNI_WRONG"),
        timeout=7,
        max_pages=5,
        detail_limit=5,
    )
    assert rows == []
    assert parser == municipal.EUMSEONG_LECTURE_PARSER
    assert meta["snapshot_complete"] is False
    assert "canonical Eumseong" in meta["configured_collection_error"]
