from __future__ import annotations

import math

from tools import sample_collect_from_yaml as collector


class FakeResponse:
    def __init__(self, *, payload: dict | None = None, text: str = "") -> None:
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.post_calls: list[dict[str, str]] = []
        self.get_calls = 0

    def post(self, _url: str, *, data: dict[str, str], timeout: int) -> FakeResponse:
        assert timeout == 20
        self.post_calls.append(data)
        store_code = data["storeCode"]
        page = int(data["curPage"])
        total = 25 if store_code == "01" else 0
        start = (page - 1) * 10
        items = [
            {
                "yearCode": "2026",
                "smstCode": "S3",
                "storeCode": store_code,
                "storeName": "본점",
                "lectCode": f"L{index:04d}",
                "lectName": f"플라워 공예 {index}",
                "lectStat": "RT",
                "lectAmtCurr": "30,000",
                "dayCodeName": "월",
                "lectHm": "10:00~11:00",
                "lectPeriod": "2026.09.01~2026.11.30",
                "inetLectPeriod": "2026.07.01~2026.08.31",
                "lectCnt": 12,
                "tchName": "강사",
                "tlectTargetMemCodeName": "대중",
            }
            for index in range(start, min(total, start + 10))
        ]
        return FakeResponse(
            payload={
                "result": "SUCCESS",
                "lectList": items,
                "param": {
                    "totalCount": total,
                    "pageSize": 10,
                },
            }
        )

    def get(self, _url: str, *, timeout: int) -> FakeResponse:
        assert timeout == 20
        self.get_calls += 1
        return FakeResponse(
            text="""
                <dl>
                  <dt>수강대상</dt><dd>성인</dd>
                  <dt>강의실</dt><dd>문화홀</dd>
                  <dt>수강료</dt><dd>20,000 원</dd>
                  <dt>접수기간</dt><dd>2026.07.01 ~ 2026.08.31</dd>
                </dl>
            """
        )


def test_shinsegae_total_pages_uses_api_count() -> None:
    assert collector.shinsegae_total_pages(
        {"param": {"totalCount": 461, "pageSize": "10"}},
        10,
    ) == 47


def test_shinsegae_collector_finishes_all_api_pages_and_required_fields(
    monkeypatch,
) -> None:
    fake_session = FakeSession()
    monkeypatch.setattr(collector, "session", lambda: fake_session)

    rows, pages, note = collector.shinsegae(1_000)

    assert len(rows) == 25
    assert pages == 3 + len(collector.SHINSEGAE_STORES) - 1
    assert fake_session.get_calls == 25
    assert "expected_rows=25" in note
    assert all(row["target"] == "성인" for row in rows)
    assert all(row["fee"] == "20,000 원" for row in rows)
    assert all(row["period"] == "2026.09.01~2026.11.30" for row in rows)
    assert all(row["apply_period"] == "2026.07.01~2026.08.31" for row in rows)
    assert all(row["venue_name"] == "문화홀" for row in rows)
    assert all(row["category"] == "미술·공예" for row in rows)
    assert all(row["schedule_raw"] == "월 10:00~11:00" for row in rows)
    assert math.ceil(25 / 10) == 3
