from __future__ import annotations

from typing import Any

from Crawler import Crawler_MunicipalYaml as municipal


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _Session:
    def __init__(self, pages: dict[int, dict[str, Any]]) -> None:
        self.headers: dict[str, str] = {}
        self.pages = pages
        self.requested_pages: list[int] = []

    def post(
        self,
        _url: str,
        *,
        json: dict[str, Any],
        **_kwargs: Any,
    ) -> _Response:
        page = int(json["pageIndex"])
        self.requested_pages.append(page)
        return _Response(self.pages[page])


def _item(number: int) -> dict[str, Any]:
    return {
        "instId": "DSS_INST_1",
        "instNm": "대구 체험기관",
        "ftrPrgrmId": f"PROGRAM_{number:03d}",
        "ftrPrgrmNm": f"대구 체험 {number}",
        "ftrPrgrmDcdNm": "체험",
        "rcptStatus": "ING",
        "chrgYn": "N",
        "prgrmBgngYmd": "2099-01-01",
        "prgrmEndYmd": "2099-01-31",
        "rcptBgngYmd": "2098-12-01",
        "rcptEndYmd": "2098-12-31",
        "trgtNm": "대구시민",
        "plcNm": "대구 체험관",
    }


def _payload(total: int, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": {"totalElements": total, "items": items}}


def _target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider="DAEGU_RESERVATION",
        name="대구광역시 체험예약",
        branch="대구광역시",
        url="https://yeyak.daegu.go.kr/expr/list",
        source="test",
    )


def _collect(monkeypatch, pages: dict[int, dict[str, Any]], *, max_pages: int):
    current_session = _Session(pages)
    monkeypatch.setattr(municipal, "session", lambda: current_session)
    rows, parser, meta = municipal.collect_daegu_expr_reservation(
        _target(),
        timeout=1,
        max_pages=max_pages,
    )
    return current_session, rows, parser, meta


def test_daegu_experience_proves_a_matching_multi_page_source_total(monkeypatch) -> None:
    first_page = [_item(number) for number in range(1, 33)]
    second_page = [_item(33)]

    current_session, rows, parser, meta = _collect(
        monkeypatch,
        {
            1: _payload(33, first_page),
            2: _payload(33, second_page),
        },
        max_pages=5,
    )

    assert parser == "daegu_expr_reservation"
    assert current_session.requested_pages == [1, 2]
    assert len(rows) == 33
    assert meta["pages"] == meta["expected_pages"] == 2
    assert meta["source_item_count"] == 33
    assert meta["unique_source_id_count"] == 33
    assert meta["pagination_complete"] is True
    assert meta["pagination_exhausted"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["source_cap_reached"] is False
    assert meta["configured_collection_error"] == ""


def test_daegu_experience_rejects_duplicate_source_ids(monkeypatch) -> None:
    first_page = [_item(number) for number in range(1, 33)]

    current_session, rows, _parser, meta = _collect(
        monkeypatch,
        {
            1: _payload(33, first_page),
            2: _payload(33, [_item(32)]),
        },
        max_pages=5,
    )

    assert current_session.requested_pages == [1, 2]
    assert len(rows) == 32
    assert meta["source_item_count"] == 33
    assert meta["unique_source_id_count"] == 32
    assert meta["duplicate_source_id_count"] == 1
    assert meta["pagination_complete"] is False
    assert meta["pagination_exhausted"] is False
    assert meta["full_snapshot_validated"] is False
    assert meta["source_cap_reached"] is False
    assert "unique source ID count 32 did not match declared 33" in meta[
        "configured_collection_error"
    ]


def test_daegu_experience_marks_a_page_budget_cap_incomplete(monkeypatch) -> None:
    current_session, rows, _parser, meta = _collect(
        monkeypatch,
        {
            1: _payload(65, [_item(number) for number in range(1, 33)]),
            2: _payload(65, [_item(number) for number in range(33, 65)]),
        },
        max_pages=2,
    )

    assert current_session.requested_pages == [1, 2]
    assert len(rows) == 64
    assert meta["expected_pages"] == 3
    assert meta["pagination_complete"] is False
    assert meta["pagination_exhausted"] is False
    assert meta["full_snapshot_validated"] is False
    assert meta["page_cap_reached"] is True
    assert meta["source_cap_reached"] is True
    assert "requires 3 pages but max_pages=2" in meta["configured_collection_error"]


def test_daegu_experience_rejects_a_premature_empty_sentinel(monkeypatch) -> None:
    current_session, rows, _parser, meta = _collect(
        monkeypatch,
        {
            1: _payload(33, [_item(number) for number in range(1, 33)]),
            2: _payload(33, []),
        },
        max_pages=5,
    )

    assert current_session.requested_pages == [1, 2]
    assert len(rows) == 32
    assert meta["empty_sentinel_reached"] is True
    assert meta["pages"] == 1
    assert meta["expected_pages"] == 2
    assert meta["pagination_complete"] is False
    assert meta["pagination_exhausted"] is False
    assert meta["full_snapshot_validated"] is False
    assert meta["source_cap_reached"] is False
    assert meta["no_current_data"] is False
    assert "page count 1 did not match declared 2" in meta[
        "configured_collection_error"
    ]


def test_daegu_experience_accepts_only_an_explicit_zero_total_empty_sentinel(
    monkeypatch,
) -> None:
    current_session, rows, _parser, meta = _collect(
        monkeypatch,
        {1: _payload(0, [])},
        max_pages=5,
    )

    assert current_session.requested_pages == [1]
    assert rows == []
    assert meta["empty_sentinel_reached"] is True
    assert meta["pages"] == meta["expected_pages"] == 0
    assert meta["source_item_count"] == 0
    assert meta["unique_source_id_count"] == 0
    assert meta["pagination_complete"] is True
    assert meta["pagination_exhausted"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["source_cap_reached"] is False
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"] == "official_api_declared_zero_items"
    assert meta["configured_collection_error"] == ""
