from __future__ import annotations

from typing import Any

import pytest

from Crawler import Crawler_MunicipalYaml as municipal
from Crawler import municipal_dongducheon
from Crawler import municipal_yeoju


class _Response:
    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict[str, Any]]:
        return self._payload


class _Session:
    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self.headers: dict[str, str] = {}
        self.payload = payload
        self.posts: list[dict[str, Any]] = []

    def post(self, _url: str, **kwargs: Any) -> _Response:
        self.posts.append(kwargs)
        return _Response(self.payload)


def _item(identity: str, sponsor: str, branch: str) -> dict[str, Any]:
    return {
        "d_sbjct_sn": identity,
        "d_sbjct_cycl_sn": "1",
        "d_sbjct_nm": f"교육 {identity}",
        "d_co_sprvsn_id": sponsor,
        "d_edu_gvmnfc": branch,
        "d_rgn": "경기도",
        "d_total_cnt": "2",
        "d_edu_bgng_dt": "2026.08.01",
        "d_edu_end_dt": "2026.08.31",
        "d_recrut_stts_nm": "모집중",
    }


def test_parent_gseek_excludes_dedicated_municipal_partitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Session(
        [
            _item("1", "G000001", "경기도 평생학습포털"),
            _item(
                "2",
                municipal_dongducheon.DONGDUCHEON_CO_SPONSOR_ID,
                municipal_dongducheon.DONGDUCHEON_BRANCH,
            ),
            _item(
                "3",
                municipal_yeoju.YEOJU_CO_SPONSOR_ID,
                municipal_yeoju.YEOJU_BRANCH,
            ),
        ]
    )
    monkeypatch.setattr(municipal, "session", lambda: fake)
    target = municipal.CrawlTarget(
        provider="GYEONGGI_GSEEK",
        name="경기도 평생학습포털 GSEEK",
        branch="경기도",
        url="https://www.gseek.kr/user/course/offline/list",
        source="test",
    )

    rows, parser, meta = municipal.collect_gyeonggi_gseek_offline(
        target,
        timeout=10,
        max_pages=1,
    )

    assert parser == "gseek_offline_api"
    assert [row["title"] for row in rows] == ["교육 1"]
    assert meta["excluded_dedicated_owner_count"] == 2
    assert meta["excluded_dedicated_owner_counts"] == {
        municipal_dongducheon.DONGDUCHEON_CO_SPONSOR_ID: 1,
        municipal_yeoju.YEOJU_CO_SPONSOR_ID: 1,
    }
    assert fake.posts[0]["data"] == {
        "s_sort_by": "1",
        "s_row_start": "1",
        "s_row_end": "10",
        "resion": "",
    }
