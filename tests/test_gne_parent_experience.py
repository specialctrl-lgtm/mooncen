from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Mapping

import pytest
import yaml

from backend.ops import region_collection as ops
from Crawler import Crawler_MunicipalYaml as router
from Crawler import municipal_gne_parent_experience as collector
from Crawler import municipal_tongyeong_experience as csec


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class _Response:
    url: str
    content: bytes = b""
    status_code: int = 200
    location: str = ""

    @property
    def headers(self) -> dict[str, str]:
        values = {"content-type": "text/html; charset=UTF-8"}
        if self.location:
            values["location"] = self.location
        return values

    @property
    def history(self) -> tuple[Any, ...]:
        return ()


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


_OPEN_IDENTITIES = {
    "10318",
    "10316",
    "10397",
    "10426",
    "10428",
    "10430",
    "10432",
    "10340",
    "10338",
}
_SCHEDULED_IDENTITIES = {"10379", "10377", "10419", "10417"}


def _fixture_rows(*, unknown_owned_identity: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for identity, institution in collector.GNE_PARENT_IDENTITY_WHITELIST.items():
        status = (
            "예약하기"
            if identity in _OPEN_IDENTITIES
            else "예약전"
            if identity in _SCHEDULED_IDENTITIES
            else "예약마감"
        )
        rows.append(
            {
                "identity": identity,
                "institution": institution,
                "title": f"학부모 체험 fixture {identity}",
                "operation": "2026.08.05. ~ 2026.12.31.",
                "reception": "2026.08.01. 09:00 ~ 2026.12.01. 18:00",
                "status": status,
            }
        )
    for identity, institution in (
        collector.GNE_PARENT_EXCLUDED_SCHOOL_VISIT_IDENTITIES.items()
    ):
        rows.append(
            {
                "identity": identity,
                "institution": institution,
                "title": f"학교 방문형 명시 제외 fixture {identity}",
                "operation": "2026.08.05. ~ 2026.12.31.",
                "reception": "2026.08.01. 09:00 ~ 2026.12.01. 18:00",
                "status": "예약하기",
            }
        )

    # The official baseline has 63 current rows outside the six-institution owner,
    # including the eight rows belonging to the independent csec provider.
    for index in range(63):
        rows.append(
            {
                "identity": str(700_000 + index),
                "institution": "교실형안전체험관" if index < 8 else "기타기관",
                "title": f"비소유 현재 fixture {index}",
                "operation": "2026.08.05. ~ 2026.12.31.",
                "reception": "2026.08.01. 09:00 ~ 2026.12.01. 18:00",
                "status": "예약하기",
            }
        )
    for index in range(133):
        rows.append(
            {
                "identity": str(800_000 + index),
                "institution": "과거기관",
                "title": f"만료 fixture {index}",
                "operation": "2025.01.01. ~ 2025.12.31.",
                "reception": "2025.01.01. 09:00 ~ 2025.12.01. 18:00",
                "status": "예약마감",
            }
        )
    assert len(rows) == 219
    for sequence, row in zip(range(219, 0, -1), rows, strict=True):
        row["sequence"] = sequence
        row["audience"] = "학생,학부모"
        row["eligible"] = "개인"
        row["method"] = "온라인"
    if unknown_owned_identity:
        rows[0]["identity"] = "999999"
    return rows


def _paging_controls(page: int, page_size: int) -> dict[str, str]:
    if page_size == collector.GNE_PARENT_PAGE_SIZE:
        controls = collector.gne_parent_post_data(page)
        controls["limitOffset"] = str((page - 1) * page_size)
        controls["maxSn"] = str(page * page_size)
        controls["minSn"] = str((page - 1) * page_size)
        return controls
    return {
        "currPage": str(page),
        "cmmnCode": "gradeSe",
        "maxSn": str(page_size),
        "pageIndex": str(page_size),
        "limitOffset": "0",
        "mi": "6927",
        "minSn": "0",
        "limitRowCo": str(page_size),
    }


def _list_html(
    rows: list[dict[str, Any]],
    *,
    page: int,
    page_size: int,
    total: int = 219,
    sentinel_text: str = collector._NO_DATA_TEXT,
) -> bytes:
    last_page = max(1, (total + page_size - 1) // page_size)
    institution_menu = "".join(
        f"<span>{institution}</span>"
        for institution in collector.GNE_PARENT_INSTITUTIONS
    )
    controls = "".join(
        f'<input name="{name}" value="{value}">'
        for name, value in _paging_controls(page, page_size).items()
    )
    if rows:
        body = []
        for row in rows:
            values = [
                row["sequence"],
                row["institution"],
                row["title"],
                row["operation"],
                row["reception"],
                row["audience"],
                row["eligible"],
                row["method"],
                (
                    f'<button onclick="goViewExprn(\'{row["identity"]}\', '
                    f"'view', this);\">{row['status']}</button>"
                ),
            ]
            body.append(
                "<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>"
            )
        body_html = "".join(body)
    else:
        body_html = f'<tr><td colspan="9">{sentinel_text}</td></tr>'
    headers = "".join(f"<th>{value}</th>" for value in collector._LIST_HEADERS)
    return f"""
<!doctype html>
<html lang="ko">
<head><title>경상남도교육청 통합예약포털 -견학/체험</title></head>
<body>
<form id="exprnListForm" method="post">
  <input name="insttId" value="">
  {institution_menu}
</form>
<div>전체 : {total}건 ({page} / {last_page})</div>
<table class="reserv-list-table">
  <thead><tr>{headers}</tr></thead>
  <tbody>{body_html}</tbody>
</table>
<form name="pagingForm" method="post" action="/yeyak/exprn/exprnList.do">
  {controls}
</form>
</body></html>
""".encode("utf-8")


def _target() -> dict[str, str]:
    return {
        "provider": collector.GNE_PARENT_PROVIDER,
        "url": collector.GNE_PARENT_URL,
    }


def _fetcher_for(
    calls: list[tuple[str, str, dict[str, str]]],
    *,
    bootstrap: bool = True,
    unknown_owned_identity: bool = False,
    sentinel_text: str = collector._NO_DATA_TEXT,
):
    rows = _fixture_rows(unknown_owned_identity=unknown_owned_identity)
    state = {"bootstrap": bootstrap}

    def fetcher(
        _session: Any,
        method: str,
        url: str,
        data: Mapping[str, str],
        _timeout: int,
    ) -> _Response:
        calls.append((method, url, dict(data)))
        if method == "GET" and state["bootstrap"]:
            state["bootstrap"] = False
            return _Response(
                url=url,
                status_code=302,
                location=collector.GNE_PARENT_BOOTSTRAP_LOCATION,
            )
        if method == "GET":
            return _Response(
                url=url,
                content=_list_html(rows[:10], page=1, page_size=10),
            )
        page = int(data["currPage"])
        start = (page - 1) * collector.GNE_PARENT_PAGE_SIZE
        selected = rows[start : start + collector.GNE_PARENT_PAGE_SIZE]
        return _Response(
            url=url,
            content=_list_html(
                selected,
                page=page,
                page_size=collector.GNE_PARENT_PAGE_SIZE,
                sentinel_text=sentinel_text,
            ),
        )

    return fetcher


def _collect(**fetcher_options: Any):
    calls: list[tuple[str, str, dict[str, str]]] = []
    session = _Session()
    rows, parser, meta = collector.collect_gne_parent_experience(
        _target(),
        today="2026-08-05",
        timeout=3,
        max_pages=10,
        detail_limit=100,
        session_factory=lambda: session,
        fetcher=_fetcher_for(calls, **fetcher_options),
    )
    return rows, parser, meta, calls, session


def test_exact_target_identity_hashes_and_safe_request_boundary() -> None:
    assert collector.GNE_PARENT_PROVIDER == "MUNI_SERVICE_GNE_GO_KR_8A9E7604"
    assert collector.GNE_PARENT_CANDIDATE_ID == "MUNI_IR_0B7CF53680D0"
    assert collector.is_gne_parent_experience_target(_target())
    assert not collector.is_gne_parent_experience_target(
        {**_target(), "provider": csec.TONGYEONG_EXPERIENCE_PROVIDER}
    )
    assert not collector.is_gne_parent_experience_target(
        {**_target(), "url": collector.GNE_PARENT_URL + "&currPage=2"}
    )

    collector._validate_request("GET", collector.GNE_PARENT_URL, {})
    collector._validate_request(
        "POST", collector.GNE_PARENT_POST_URL, collector.gne_parent_post_data(3)
    )
    refused = [
        ("GET", collector.GNE_PARENT_BOOTSTRAP_LOCATION, {}),
        (
            "GET",
            "https://service.gne.go.kr/yeyak/exprn/exprnInfo.do?mi=6927",
            {},
        ),
        (
            "POST",
            collector.GNE_PARENT_POST_URL,
            {**collector.gne_parent_post_data(2), "limitOffset": "50"},
        ),
        (
            "POST",
            "https://service.gne.go.kr/yeyak/exprn/apply.do",
            collector.gne_parent_post_data(1),
        ),
    ]
    for method, url, data in refused:
        with pytest.raises(collector.GneParentExperienceContractError):
            collector._validate_request(method, url, data)


def test_complete_219_snapshot_selects_exact_21_and_never_calls_unsafe_routes() -> None:
    rows, parser, meta, calls, session = _collect()

    assert parser == collector.GNE_PARENT_PARSER
    assert {row["source_course_id"] for row in rows} == set(
        collector.GNE_PARENT_IDENTITY_WHITELIST
    )
    assert len(rows) == 21
    assert meta["source_total"] == 219
    assert meta["source_current_count"] == 86
    assert meta["source_expired_count"] == 133
    assert meta["returned_count"] == 21
    assert meta["page_row_counts"] == [50, 50, 50, 50, 19]
    assert meta["data_pages"] == 5
    assert meta["sentinel_page"] == 6
    assert meta["municipality_counts"] == {
        "4827000000": 4,
        "4831000000": 5,
        "4874000000": 1,
        "4882000000": 8,
        "4888000000": 1,
        "4889000000": 2,
    }
    assert meta["status_counts"] == {"CLOSED": 8, "OPEN": 9, "SCHEDULED": 4}
    assert meta["excluded_geoje_school_visit_count"] == 2
    assert meta["excluded_non_owned_institution_count"] == 63
    assert meta["excluded_existing_csec_current_count"] == 8
    assert meta["existing_csec_source_identity_overlap_count"] == 0
    assert meta["output_identity_sha256"] == (
        "2e488e6d90fc32267e78a1f53296c20b036d9940bfa5552f9374bd026c5e6649"
    )
    assert meta["logical_requests"] == meta["list_requests"] == len(calls) == 12
    assert meta["sso_bootstrap_locations_observed_not_followed"] == 1
    assert calls[0] == calls[1] == ("GET", collector.GNE_PARENT_URL, {})
    post_pages = [int(data["currPage"]) for method, _, data in calls if method == "POST"]
    assert post_pages == [1, 2, 3, 4, 5, 6, 1, 5, 6]
    for method, url, data in calls:
        if method == "GET":
            assert url == collector.GNE_PARENT_URL and data == {}
        else:
            assert url == collector.GNE_PARENT_POST_URL
            assert data == collector.gne_parent_post_data(int(data["currPage"]))
        assert not any(
            token in url.lower()
            for token in (
                "exprninfo",
                "apply",
                "sso/",
                "login",
                "auth",
                "member",
                "applicant",
                "attachment",
                "download",
            )
        )
    for key in (
        "unsafe_endpoint_requests",
        "detail_requests",
        "application_endpoint_requests",
        "sso_endpoint_requests",
        "login_endpoint_requests",
        "auth_endpoint_requests",
        "member_endpoint_requests",
        "applicant_endpoint_requests",
        "attachment_endpoint_requests",
        "download_endpoint_requests",
        "pii_endpoint_requests",
    ):
        assert meta[key] == 0
    assert meta["snapshot_complete"] is meta["full_snapshot_validated"] is True
    assert session.closed is True

    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["classification_locked"] is True for row in rows)
    assert all(row["application_url"] == "" for row in rows)
    assert all(row["reservation_available"] is False for row in rows)
    assert all(row["address"] == row["venue_address"] == "" for row in rows)


def test_new_current_identity_at_owned_institution_fails_atomically() -> None:
    rows, _, meta, _, session = _collect(unknown_owned_identity=True)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "outside exact whitelist: 999999" in meta["configured_collection_error"]
    assert session.closed is True


def test_exact_empty_sentinel_and_collection_limits_fail_closed() -> None:
    rows, _, meta, _, _ = _collect(sentinel_text="일시적으로 데이터가 없습니다")
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sentinel" in meta["configured_collection_error"] or "column count" in meta[
        "configured_collection_error"
    ]

    calls: list[tuple[str, str, dict[str, str]]] = []
    rows, _, meta = collector.collect_gne_parent_experience(
        _target(),
        today="2026-08-05",
        max_pages=5,
        detail_limit=100,
        session_factory=_Session,
        fetcher=_fetcher_for(calls),
    )
    assert rows == []
    assert "truncates declared ledger" in meta["configured_collection_error"]

    calls = []
    rows, _, meta = collector.collect_gne_parent_experience(
        _target(),
        today="2026-08-05",
        max_pages=10,
        detail_limit=20,
        session_factory=_Session,
        fetcher=_fetcher_for(calls),
    )
    assert rows == []
    assert "detail_limit truncates exact current parent owner" in meta[
        "configured_collection_error"
    ]


def test_parent_owner_is_disjoint_from_existing_csec_owner() -> None:
    assert collector.GNE_PARENT_PROVIDER != csec.TONGYEONG_EXPERIENCE_PROVIDER
    assert set(collector.GNE_PARENT_IDENTITY_WHITELIST).isdisjoint(
        csec.TONGYEONG_EXPERIENCE_CURRENT_IDENTITY_WHITELIST
    )
    assert collector.GNE_PARENT_URL != csec.TONGYEONG_EXPERIENCE_URL


def test_exact_router_dispatch_injects_managed_session_and_dedupe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = ([{"provider": collector.GNE_PARENT_PROVIDER}], "fixture", {})
    captured: dict[str, Any] = {}

    def fake_collect(target: Any, **kwargs: Any):
        captured["target"] = target
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(collector, "collect_gne_parent_experience", fake_collect)
    target = router.CrawlTarget(
        provider=collector.GNE_PARENT_PROVIDER,
        name="경남교육청 학부모 체험·견학",
        branch="경남교육청 학부모 체험기관 6개 시군",
        url=collector.GNE_PARENT_URL,
        source="test",
        priority=1,
        region="경상남도",
        extra={},
    )
    assert router.collect_from_url(
        target,
        timeout=7,
        max_depth=0,
        max_pages=10,
        detail_limit=100,
    ) == expected
    assert captured["target"] is target
    assert captured["timeout"] == 7
    assert captured["max_pages"] == 10
    assert captured["detail_limit"] == 100
    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])


def test_target_operational_six_coverage_rows_and_ops_scope_are_exact() -> None:
    provider = collector.GNE_PARENT_PROVIDER
    target_data = yaml.safe_load(
        (ROOT / "config/crawl_targets/municipal_integrated_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )["targets"]
    matches = [row for row in target_data if row.get("provider") == provider]
    assert len(matches) == 1
    target = matches[0]
    expected_codes = set(collector.GNE_PARENT_LIVE_AUDIT_BASELINE["municipality_counts"])
    assert target["candidate_id"] == collector.GNE_PARENT_CANDIDATE_ID
    assert target["url"] == collector.GNE_PARENT_URL
    assert target["crawler_module"] == "Crawler.municipal_gne_parent_experience"
    assert target["crawler_callable"] == "collect_gne_parent_experience"
    assert target["ops_scopes"] == ["experience"]
    assert target["service_group"] == "체험"
    assert target["full_snapshot_required"] is True
    assert target["last_quality"]["collected"] == 21
    assert set(target["row_municipality_codes"]) == expected_codes
    assert {row["code"] for row in target["covered_municipalities"]} == expected_codes

    operational = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )["entries"]
    operational_matches = [row for row in operational if row.get("provider") == provider]
    assert len(operational_matches) == 1
    assert operational_matches[0]["target_url"] == collector.GNE_PARENT_URL
    assert operational_matches[0]["row_count"] == 21
    assert {row["code"] for row in operational_matches[0]["municipalities"]} == (
        expected_codes
    )

    coverage = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_coverage.yaml").read_text(
            encoding="utf-8"
        )
    )["municipalities"]
    for code in expected_codes:
        municipality = next(row for row in coverage if row.get("code") == code)
        for field in ("owner_providers", "promoted_providers", "yaml_owner_providers"):
            assert provider in municipality[field]
        assert any(
            evidence.get("provider") == provider
            and evidence.get("target_url") == collector.GNE_PARENT_URL
            and evidence.get("row_count") == 21
            for evidence in municipality["evidence"]
        )

    reference = ops._region_reference()
    for _, _, full_name in collector.GNE_PARENT_INSTITUTIONS.values():
        assert provider in reference.configured_by_scope["experience"][full_name]
        assert provider not in reference.configured_by_scope["education"].get(
            full_name, ()
        )
    assert provider not in reference.unmapped_configured_by_scope["experience"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_GNE_PARENT_EXPERIENCE") != "1",
    reason="set RUN_LIVE_GNE_PARENT_EXPERIENCE=1 for the audited official list probe",
)
def test_live_official_parent_snapshot_uses_only_safe_list_requests() -> None:
    rows, parser, meta = collector.collect_gne_parent_experience(
        _target(),
        today="2026-08-05",
        timeout=30,
        max_pages=10,
        detail_limit=100,
    )
    assert parser == collector.GNE_PARENT_PARSER
    assert len(rows) == meta["returned_count"] == 21
    assert meta["source_total"] == 219
    assert meta["source_current_count"] == 86
    assert meta["page_row_counts"] == [50, 50, 50, 50, 19]
    assert meta["unsafe_endpoint_requests"] == 0
    assert meta["detail_requests"] == 0
    assert meta["application_endpoint_requests"] == 0
    assert meta["snapshot_complete"] is True
