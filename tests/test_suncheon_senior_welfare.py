from __future__ import annotations

from datetime import date
import io
from types import SimpleNamespace
from typing import Any
import zipfile

import pytest
import requests

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import suncheon_senior_welfare as suncheon


def _cell(
    row: int,
    column: int,
    text: str,
    *,
    row_span: int = 1,
    column_span: int = 1,
) -> str:
    return f"""
    <tc>
      <cellAddr rowAddr="{row}" colAddr="{column}"/>
      <cellSpan rowSpan="{row_span}" colSpan="{column_span}"/>
      <p><run><t>{text}</t></run></p>
    </tc>
    """


def _table(row_count: int, column_count: int, rows: list[list[str]]) -> str:
    return (
        f'<tbl rowCnt="{row_count}" colCnt="{column_count}">'
        + "".join(f"<tr>{''.join(row)}</tr>" for row in rows)
        + "</tbl>"
    )


def _hwpx(*, missing_grid_time: bool = False) -> bytes:
    headers = [
        _cell(0, index, value)
        for index, value in enumerate(
            ("", "\uc6d4", "\ud654", "\uc218", "\ubaa9", "\uae08")
        )
    ]
    yongdang = _table(
        2,
        6,
        [
            headers,
            [
                _cell(1, 0, "\uc624 \uc804"),
                _cell(
                    1,
                    1,
                    (
                        "\u25aa \uc694\uac001\ubc18 "
                        "(9:00-10:00) \uae40\uac15\uc0ac / 3\uce35 \ub300\uac15\ub2f9"
                    ),
                ),
            ],
        ],
    )
    dongbu = _table(
        2,
        18,
        [
            [
                _cell(0, 0, ""),
                _cell(0, 1, ""),
                _cell(0, 2, ""),
                _cell(0, 3, "\uc6d4", column_span=3),
                _cell(0, 6, "\ud654", column_span=3),
                _cell(0, 9, "\uc218", column_span=3),
                _cell(0, 12, "\ubaa9", column_span=3),
                _cell(0, 15, "\uae08", column_span=3),
            ],
            [
                _cell(1, 0, "\uc624\uc804"),
                _cell(1, 1, "2\uce35"),
                _cell(1, 2, "\uad50\uc721\uc2e41"),
                _cell(
                    1,
                    3,
                    "\uc624\uce74\ub9ac\ub098 (\ub098\uc601\uc219)",
                ),
                _cell(1, 4, "" if missing_grid_time else "10:00-12:00"),
                _cell(1, 5, ""),
            ],
        ],
    )
    nambu = _table(
        2,
        6,
        [
            headers,
            [
                _cell(1, 0, "\uc624 \ud6c4"),
                _cell(
                    1,
                    5,
                    (
                        "\u25aa \ub178\ub798 1\ubc18 "
                        "(1:00-2:00) \ubc15\uac15\uc0ac / \ub300\uac15\ub2f9"
                    ),
                ),
            ],
        ],
    )
    section = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<section>{yongdang}{dongbu}{nambu}</section>"
    ).encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("Contents/section0.xml", section)
    return output.getvalue()


def _response(
    *,
    url: str,
    text: str = "",
    content: bytes | None = None,
) -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response.url = url
    response._content = content if content is not None else text.encode("utf-8")
    response.encoding = "utf-8"
    return response


def _list_html() -> str:
    return """
    <table class="bbsList"><tbody>
      <tr>
        <td>\uacf5\uc9c0</td><td>\ub0a8\ubd80\ub178\uc778\ubcf5\uc9c0\uad00</td>
        <td><a href="/silver/community/0001/0001/?boardId=bbs_0000000000000063&amp;mode=view&amp;cntId=203">
          2026 \ud558\ubc18\uae30 \ud504\ub85c\uadf8\ub7a8 \uc218\uac15\uc0dd \ubaa8\uc9d1
        </a></td>
        <td>\uc624\uc0c1\ubbf8</td><td>2026-06-11</td><td>270</td>
      </tr>
      <tr>
        <td>\uacf5\uc9c0</td><td>\ub0a8\ubd80\ub178\uc778\ubcf5\uc9c0\uad00</td>
        <td><a href="/silver/community/0001/0001/?boardId=bbs_0000000000000063&amp;mode=view&amp;cntId=192">
          2026\ub144 \uc0c1\ubc18\uae30 \ud504\ub85c\uadf8\ub7a8 \uc218\uac15\uc0dd \ubaa8\uc9d1
        </a></td>
        <td>\uc624\uc0c1\ubbf8</td><td>2025-12-16</td><td>600</td>
      </tr>
    </tbody></table>
    """


def _detail_html(
    *,
    operation_period: str = "2026. 7. 6.(\uc6d4) ~ 12. 11.(\uae08)",
) -> str:
    return f"""
    <table class="bbsView">
      <tr><th>2026 \ud558\ubc18\uae30 \ud504\ub85c\uadf8\ub7a8 \uc218\uac15\uc0dd \ubaa8\uc9d1</th></tr>
      <tr><th>\ucca8\ubd80</th><td>
        <a href="javascript:Jnit_boardDownload('/board/file/bbs_0000000000000063/203/FILE_1/timetable;jsessionid=ABC','x','1');">
          2026\ub144 \ud558\ubc18\uae30 3\uac1c \ub178\uc778\ubcf5\uc9c0\uad00 \ud504\ub85c\uadf8\ub7a8 \uc2dc\uac04\ud45c.hwpx
          ( \ub2e4\uc6b4\ub85c\ub4dc 25 \ud68c )
        </a>
      </td></tr>
      <tr><td><div class="content">
        1. \ubaa8\uc9d1\uae30\uac04: 2025. 6. 15.(\uc6d4) ~ 6. 19.(\uae08)
        2. \uc6b4\uc601\uae30\uac04: {operation_period}
        3. \ubaa8\uc9d1\uc778\uc6d0: 30\uba85 (3\uac1c\ubd84\uc57c, 3\uac15\uc88c, 3\uac1c \ubc18)
        - \uc6a9\ub2f9\ubcf5\uc9c0\uad00: 10\uba85 / (1\uac1c \ubd84\uc57c, 1\uac1c \uac15\uc88c 1\uac1c \ubc18)
        - \ub3d9\ubd80\ubcf5\uc9c0\uad00: 10\uba85 / (1\uac1c \ubd84\uc57c, 1\uac1c \uac15\uc88c 1\uac1c \ubc18)
        - \ub0a8\ubd80\ubcf5\uc9c0\uad00: 10\uba85 / (1\uac1c \ubd84\uc57c, 1\uac1c \uac15\uc88c 1\uac1c \ubc18)
        4. \uc2e0\uccad\uc790\uaca9: 3\uac1c \ubcf5\uc9c0\uad00 \ub4f1\ub85d\ud68c\uc6d0
        5. \uad6c\ube44\uc11c\ub958: \uc2e0\ubd84\uc99d
        6. \uc218\uac15 \uc6b0\uc120\uc21c\uc704: \uc2e0\uaddc\uc790
        7. \uc2e0\uccad\ubc29\ubc95: \ubcf8\uc778 \ubc29\ubb38\uc811\uc218
      </div></td></tr>
    </table>
    """


class FakeSession:
    def __init__(self, *, expired: bool = False) -> None:
        self.closed = False
        self.urls: list[str] = []
        self.expired = expired

    def get(self, url: str, timeout: int) -> requests.Response:
        assert timeout == 7
        self.urls.append(url)
        if url in suncheon.NOTICE_LIST_URLS:
            return _response(url=url, text=_list_html())
        if "cntId=203" in url:
            period = (
                "2026. 1. 1.(\ubaa9) ~ 2026. 1. 31.(\ud1a0)"
                if self.expired
                else "2026. 7. 6.(\uc6d4) ~ 12. 11.(\uae08)"
            )
            return _response(url=url, text=_detail_html(operation_period=period))
        if url.endswith("/timetable"):
            return _response(url=url, content=_hwpx())
        raise AssertionError(f"unexpected URL: {url}")

    def close(self) -> None:
        self.closed = True


class FlakySession(FakeSession):
    def __init__(self) -> None:
        super().__init__()
        self.list_attempts = 0

    def get(self, url: str, timeout: int) -> requests.Response:
        if url in suncheon.NOTICE_LIST_URLS:
            self.list_attempts += 1
            if self.list_attempts == 1:
                raise requests.ConnectTimeout("temporary")
        return super().get(url, timeout)


def _target(url: str = suncheon.LIST_URL) -> Any:
    return SimpleNamespace(
        provider=suncheon.PROVIDER,
        url=url,
        branch="순천시 3개 노인복지관",
    )


def test_collects_latest_term_three_branches_and_required_fields() -> None:
    fake = FakeSession()

    rows, parser, meta = suncheon.collect(
        _target(),
        timeout=7,
        max_pages=3,
        detail_limit=2,
        dedupe_rows=lambda values: values,
        session_factory=lambda: fake,
        today=date(2026, 7, 28),
    )

    assert parser == suncheon.PARSER
    assert len(rows) == 3
    assert all("cntId=192" not in url for url in fake.urls)
    assert all(url in fake.urls for url in suncheon.NOTICE_LIST_URLS)
    assert fake.closed is True
    first = rows[0]
    assert first["target"] == "3\uac1c \ubcf5\uc9c0\uad00 \ub4f1\ub85d\ud68c\uc6d0"
    assert first["fee"] == "\uc218\uac15\ub8cc \ubcf5\uc9c0\uad00 \ubb38\uc758"
    assert first["period"] == "2026-07-06 ~ 2026-12-11"
    assert first["venue_name"] == "\uc6a9\ub2f9\ub178\uc778\ubcf5\uc9c0\uad00"
    assert first["category"].startswith(
        "\ub178\uc778\ubcf5\uc9c0\uad00 \ud3c9\uc0dd\uad50\uc721/"
    )
    assert "09:00~10:00" in first["schedule_raw"]
    assert rows[2]["schedule_raw"].startswith("\uae08 13:00~14:00")
    assert first["apply_period"] == "2026-06-15 ~ 2026-06-19"
    assert (
        first["raw_fields"]["apply_year_normalized_from_notice_typo"]
        is True
    )
    assert meta["schedule_slots"] == 3
    assert meta["schedule_groups"] == 3
    assert meta["declared_enrollment_classes"] == 3
    assert meta["published_schedule_group_difference"] == 0
    assert len({row["raw_url"] for row in rows}) == len(rows)
    assert all(
        row["raw_url"].startswith(
            "https://www.sc.go.kr/silver/community/"
        )
        and row["raw_url"].endswith(
            f"#course-{row['provider_course_id']}"
        )
        for row in rows
    )
    normalized = generated.normalize_collected_rows(
        rows,
        _target(),
        maximum_rows=10,
    )
    assert {row["raw_url"] for row in normalized} == {
        row["raw_url"] for row in rows
    }


def test_hwpx_grid_time_is_required() -> None:
    with pytest.raises(
        suncheon.SuncheonSeniorContractError,
        match="grid timetable time changed",
    ):
        suncheon.parse_hwpx_timetable(_hwpx(missing_grid_time=True))


def test_invalid_hwpx_table_count_fails_closed() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "Contents/section0.xml",
            b'<section><tbl rowCnt="2" colCnt="6"/></section>',
        )

    with pytest.raises(
        suncheon.SuncheonSeniorContractError,
        match="instead of 3",
    ):
        suncheon.parse_hwpx_timetable(output.getvalue())


def test_expired_latest_notice_is_complete_no_current_data() -> None:
    fake = FakeSession(expired=True)

    rows, _parser, meta = suncheon.collect(
        _target(),
        timeout=7,
        max_pages=3,
        detail_limit=2,
        dedupe_rows=lambda values: values,
        session_factory=lambda: fake,
        today=date(2026, 7, 28),
    )

    assert rows == []
    assert not any(url.endswith("/timetable") for url in fake.urls)
    assert meta["no_current_data"] is True
    assert meta["attachment_pages"] == 0


def test_unreviewed_target_url_fails_before_network() -> None:
    fake = FakeSession()

    with pytest.raises(
        suncheon.SuncheonSeniorContractError,
        match="reviewed official notice list",
    ):
        suncheon.collect(
            _target("https://www.sc.go.kr/silver/service"),
            timeout=7,
            max_pages=3,
            detail_limit=2,
            dedupe_rows=lambda values: values,
            session_factory=lambda: fake,
            today=date(2026, 7, 28),
        )
    assert fake.urls == []


def test_transient_official_connection_failure_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FlakySession()
    monkeypatch.setattr(suncheon.time, "sleep", lambda _seconds: None)

    rows, _parser, _meta = suncheon.collect(
        _target(),
        timeout=7,
        max_pages=3,
        detail_limit=2,
        dedupe_rows=lambda values: values,
        session_factory=lambda: fake,
        today=date(2026, 7, 28),
    )

    assert len(rows) == 3
    assert fake.list_attempts == 2
