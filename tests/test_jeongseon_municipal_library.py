from __future__ import annotations

from dataclasses import dataclass
import json
import os
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_jeongseon_municipal_library as library


@dataclass
class Target:
    provider: str
    url: str
    candidate_id: str = library.JEONGSEON_MUNICIPAL_LIBRARY_CANDIDATE_ID


class DummySession:
    def close(self) -> None:
        pass


def _target() -> Target:
    return Target(
        library.JEONGSEON_MUNICIPAL_LIBRARY_PROVIDER,
        library.JEONGSEON_MUNICIPAL_LIBRARY_URL,
    )


def _item(index: int, *, kind: str = "LECTURE") -> dict:
    identity = 400 - index
    return {
        "id": identity,
        "libraryId": 1,
        "programType": kind,
        "lectureLocation": "도서관 3층 배움1실 (평생학습관)",
        "subTitle": "정선 인문학 강좌" if kind == "LECTURE" else "정선 문화행사",
        "title": f"정선군립도서관 프로그램 {identity}",
        "teacher": "비공개 강사",
        "applicationStartDate": "2026-07-20 09:00",
        "applicationEndDate": "2026-08-20 18:00",
        "eventStartDate": "2026-08-01 10:00",
        "eventEndDate": "2026-08-31 12:00",
        "price": None,
        "participantTarget": "정선군민 누구나",
        "participantAgeLimit": None,
        "participantCount": 3,
        "participantCountLimit": 20,
        "waitApplicationCount": 0,
        "waitApplicationCountLimit": 5,
        "applyYn": "Y",
        "operType": "W",
        "operDt": "토",
        "content": '<input name="applicant" value="비공개 신청자"><p>상세 본문</p>',
        "attachments": [{"id": 999, "name": "private.pdf"}],
    }


def _items(count: int = 12) -> list[dict]:
    return [_item(index, kind="EVENT" if index == 1 else "LECTURE") for index in range(count)]


class FixtureSite:
    def __init__(self, values: list[dict] | None = None, **flags: bool):
        self.values = values or _items()
        self.flags = flags
        self.calls: list[str] = []

    def __call__(self, _session: DummySession, url: str, timeout: int) -> str:
        assert timeout > 0
        self.calls.append(url)
        parsed = urlparse(url)
        suffix = parsed.path.removeprefix(library.JEONGSEON_MUNICIPAL_LIBRARY_API_PATH + "/")
        if suffix.isdigit():
            item = dict(next(value for value in self.values if value["id"] == int(suffix)))
            if self.flags.get("detail_drift") and item["id"] == self.values[0]["id"]:
                item["title"] += " 변경"
            if self.flags.get("wrong_detail_owner"):
                item["libraryId"] = 2
            return json.dumps(item, ensure_ascii=False)
        page = int(parse_qs(parsed.query)["currentPageNo"][0])
        total = len(self.values) - (1 if self.flags.get("total_drift") and page > 1 else 0)
        page_count = (total + 9) // 10
        start = (page - 1) * 10
        rows = [dict(item) for item in self.values[start : start + 10]]
        if self.flags.get("wrong_list_owner") and rows and page == 1:
            rows[0]["libraryId"] = 2
        if self.flags.get("bad_date") and rows and page == 1:
            rows[0]["eventEndDate"] = "not-a-date"
        return json.dumps(
            {
                "pagination": {
                    "currentPageNo": page,
                    "recordCountPerPage": 10,
                    "pageSize": 10,
                    "totalRecordCount": total,
                    "totalPageCount": page_count,
                    "libraryId": 1,
                },
                "items": rows,
                "applicants": [{"name": "비공개 신청자", "phone": "010-9999-9999"}],
            },
            ensure_ascii=False,
        )


def _collect(site: FixtureSite, **kwargs):
    return library.collect_jeongseon_municipal_library(
        _target(),
        today="2026-07-23",
        now="2026-07-23T12:00:00",
        session_factory=DummySession,
        fetcher=site,
        **kwargs,
    )


def test_owner_urls_and_read_only_boundary() -> None:
    assert library.is_jeongseon_municipal_library_target(_target())
    assert not library.is_jeongseon_municipal_library_target(Target("wrong", _target().url))
    assert library._request_kind(library.municipal_library_api_url(1)) == "list"
    assert library._request_kind(library.municipal_library_detail_api_url(254)) == "detail"
    assert library.municipal_library_detail_url(254).endswith("/254?menuIds=10%2C21")
    with pytest.raises(library.JeongseonMunicipalLibraryContractError):
        library._request_kind("https://lib.jeongseon.go.kr/api/my/culture/254/applies/1")
    with pytest.raises(library.JeongseonMunicipalLibraryContractError):
        library._request_kind("https://lib.jeongseon.go.kr/culture/events/254/apply")


def test_complete_all_page_snapshot_and_production_classification() -> None:
    rows, parser, meta = _collect(FixtureSite())
    assert parser == library.JEONGSEON_MUNICIPAL_LIBRARY_PARSER
    assert len(rows) == meta["source_rows"] == meta["source_total"] == 12
    assert meta["data_pages"] == 2
    assert meta["page_counts"] == {1: 10, 2: 2}
    assert meta["empty_sentinel_page"] == 3
    assert meta["empty_sentinel_verified"] is True
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["list_requests"] == 5
    assert meta["detail_requests"] == 12
    assert meta["application_endpoint_requests"] == 0
    assert len(meta["source_identity_sha256"]) == 64
    assert meta["source_branch_counts"] == {"정선군립도서관": 12}
    assert rows[0]["provider_course_id"] == "jeongseon-library:400"
    assert rows[0]["branch"] == "정선군립도서관"
    assert rows[0]["domain_category"] == "교육·강좌"
    assert rows[0]["service_group"] == "공공강좌"
    assert rows[1]["domain_category"] == "체험·견학"
    assert rows[1]["service_group"] == "체험"
    assert all(row["collection_category"] == "공공예약" for row in rows)
    assert all(row["source_group"] == "municipal_reservation" for row in rows)
    assert all(row["municipality_code"] == "5177000000" for row in rows)
    assert rows[0]["source_status"] == "접수중"
    assert rows[0]["reservation_available"] is True
    assert rows[0]["application_url"]


def test_complete_ledger_with_no_current_rows_is_successful_empty_snapshot() -> None:
    site = FixtureSite()
    rows, parser, meta = library.collect_jeongseon_municipal_library(
        _target(),
        today="2099-01-01",
        now="2099-01-01T12:00:00",
        session_factory=DummySession,
        fetcher=site,
    )
    assert parser == library.JEONGSEON_MUNICIPAL_LIBRARY_PARSER
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"]
    assert meta["details_complete"] is True
    assert meta["detail_requests"] == 0


@pytest.mark.parametrize(
    "site,error",
    [
        (FixtureSite(wrong_list_owner=True), "required fields/owner changed"),
        (FixtureSite(wrong_detail_owner=True), "required fields/owner changed"),
        (FixtureSite(detail_drift=True), "list/detail mismatch"),
        (FixtureSite(bad_date=True), "invalid event end"),
        (FixtureSite(total_drift=True), "pagination drift"),
    ],
)
def test_contract_drift_is_fail_closed(site: FixtureSite, error: str) -> None:
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error in meta["configured_collection_error"]


def test_expired_rows_do_not_consume_detail_budget() -> None:
    expired = _item(-1)
    expired["id"] = 500
    expired["eventStartDate"] = "2026-01-01 10:00"
    expired["eventEndDate"] = "2026-01-02 12:00"
    rows, _, meta = _collect(FixtureSite([expired, *_items(2)]), detail_limit=2)
    assert len(rows) == 2
    assert meta["source_rows"] == 3
    assert meta["expired_source_count"] == 1
    assert not any(url.endswith("/500") for url in FixtureSite([expired]).calls)


def test_caps_wrong_owner_and_dedupe_fail_before_partial_save() -> None:
    rows, _, meta = _collect(FixtureSite(), max_pages=2)
    assert rows == []
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _, meta = _collect(FixtureSite(), detail_limit=11)
    assert rows == []
    assert "detail_limit cap" in meta["configured_collection_error"]

    rows, _, meta = _collect(FixtureSite(), dedupe_fn=lambda values: list(values)[:-1])
    assert rows == []
    assert "dedupe changed official identity cardinality" in meta["configured_collection_error"]

    rows, _, meta = library.collect_jeongseon_municipal_library(
        Target("wrong", library.JEONGSEON_MUNICIPAL_LIBRARY_URL),
        fetcher=lambda *_: pytest.fail("wrong owner must not fetch"),
    )
    assert rows == []
    assert "registered Jeongseon municipal-library owner" in meta["configured_collection_error"]


def test_content_applicant_and_attachment_payloads_are_not_persisted() -> None:
    rows, _, meta = _collect(FixtureSite())
    payload = repr(rows)
    assert "비공개 신청자" not in payload
    assert "010-9999-9999" not in payload
    assert "private.pdf" not in payload
    assert "상세 본문" not in payload
    assert all(row["description"] == row["title"] for row in rows)
    assert meta["pii_values_persisted"] == 0


@pytest.mark.skipif(
    os.getenv("JEONGSEON_MUNICIPAL_LIBRARY_LIVE") != "1",
    reason="set JEONGSEON_MUNICIPAL_LIBRARY_LIVE=1 for the live audit",
)
def test_live_official_complete_snapshot() -> None:
    rows, parser, meta = library.collect_jeongseon_municipal_library(
        _target(), timeout=40, max_pages=30, detail_limit=300
    )
    assert parser == library.JEONGSEON_MUNICIPAL_LIBRARY_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_rows"] == meta["source_total"]
    assert meta["snapshot_complete"] is True
    assert rows
    assert all(row["branch"] == "정선군립도서관" for row in rows)
