from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Mapping

import pytest
import yaml

from backend.ops import region_collection as ops
from Crawler import Crawler_MunicipalYaml as router
from Crawler import municipal_tongyeong_experience as collector


@dataclass
class _Response:
    url: str
    content: bytes = b""
    status_code: int = 200
    location: str = ""

    @property
    def headers(self) -> dict[str, str]:
        headers = {"content-type": "text/html; charset=UTF-8"}
        if self.location:
            headers["location"] = self.location
        return headers

    @property
    def history(self) -> tuple[Any, ...]:
        return ()


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


_ROWS = [
    (
        12,
        "10126",
        "[창원] 삼계중 교실형 안전체험관 (심폐소생술, 교통안전 등)",
        "2026.04.13. ~ 2026.12.04.",
        "2026.04.06. 06:00 ~ 2026.11.23. 00:00",
        "학생 (초 4 ~ 중3)",
        "학교",
        "예약하기",
    ),
    (
        11,
        "10107",
        "[함양] 안의초 교실형 안전체험관 (안전체험)",
        "2026.04.13. ~ 2026.11.27.",
        "2026.04.06. 09:00 ~ 2026.10.12. 00:00",
        "유아,학생,학부모,교원 (유아 ~ 중 3학년)",
        "유치원,학교,기관",
        "예약하기",
    ),
    (
        10,
        "10151",
        "[창원] 팔룡초 교실형 안전체험관",
        "2026.04.20. ~ 2026.11.26.",
        "2026.04.06. 10:00 ~ 2026.10.31. 17:00",
        "유아,학생 (유아~ 초 6학년)",
        "유치원,학교,기타",
        "예약하기",
    ),
    (
        9,
        "10120",
        "[통영] 충무초 교실형 안전체험관 (안단테 안전체험)",
        "2026.04.20. ~ 2026.12.11.",
        "2026.04.06. 09:00 ~ 2026.12.04. 16:00",
        "유아,학생 (유아 ~ 초 6학년)",
        "유치원,학교",
        "예약하기",
    ),
    (
        8,
        "10173",
        "[김해] 영운초 교실형 안전체험관 ((화,목))",
        "2026.05.01. ~ 2026.11.30.",
        "2026.04.06. 09:00 ~ 2026.11.05. 18:00",
        "유아,학생 (유아~초6)",
        "유치원,학교",
        "예약하기",
    ),
    (
        7,
        "10140",
        "[창원] 내동초 교실형 안전체험관",
        "2026.05.01. ~ 2026.11.30.",
        "2026.04.20. 09:00 ~ 2026.11.30. 00:00",
        "유아,학생 (유아 ~ 초 6학년)",
        "유치원,학교,기타",
        "예약하기",
    ),
    (
        6,
        "10150",
        "[함안] 함안중 교실형 안전체험관 (교실형 안전체험관)",
        "2026.09.02. ~ 2026.11.27.",
        "2026.04.06. 09:00 ~ 2026.10.30. 00:00",
        "학생 (초 5학년 ~ 고 1학년)",
        "학교",
        "예약하기",
    ),
    (
        5,
        "10170",
        "[양산] 삼성초 교실형 안전체험관 (1학기 체험(~7월 14일))",
        "2026.04.14. ~ 2026.07.14.",
        "2026.04.06. 08:40 ~ 2026.07.03. 16:40",
        "학생 (관내 초등학생 1~4학년)",
        "기타,관내학교",
        "예약마감",
    ),
    (
        4,
        "10127",
        "[거제] 옥포초 교실형 안전체험관 (2026년 1학기(화/목요일))",
        "2026.05.04. ~ 2026.07.16.",
        "2026.04.06. 09:00 ~ 2026.07.01. 00:00",
        "유아,학생 (병설유치원유아 ~ 초 6학년)",
        "유치원,학교",
        "예약마감",
    ),
    (
        3,
        "10108",
        "[진주] 은하수초 교실형 안전체험관 (2026년1학기)",
        "2026.05.11. ~ 2026.07.17.",
        "2026.04.20. 09:00 ~ 2026.07.03. 18:00",
        "유아,학생 (유아 ~ 초 6학년)",
        "유치원,학교",
        "예약마감",
    ),
    (
        2,
        "10123",
        "[김해] 김해동광초 교실형 안전체험관 (5월~7월)",
        "2026.05.13. ~ 2026.07.15.",
        "2026.04.15. 00:00 ~ 2026.07.01. 00:00",
        "학생 (유아 ~ 초 6학년)",
        "유치원,학교",
        "예약마감",
    ),
    (
        1,
        "10392",
        "[양산] 삼성초 교실형 안전체험관 (2학기 체험(~10.15., 차량 포함))",
        "2026.09.10. ~ 2026.11.12.",
        "2026.07.15. 08:40 ~ 2026.07.24. 16:40",
        "학생 (관내 초등학생 1~2학년)",
        "기타,관내학교 (담당 교사)",
        "예약마감",
    ),
]


def _list_html(
    rows: list[tuple[Any, ...]],
    page_size: int,
    *,
    declared_total: int | None = None,
    unsafe_header: bool = False,
) -> bytes:
    total = len(rows) if declared_total is None else declared_total
    last_page = max(1, (total + page_size - 1) // page_size)
    headers = list(collector._LIST_HEADERS)
    if unsafe_header:
        headers.append("신청자 전화번호")
    body: list[str] = []
    for sequence, identity, title, operation, reception, audience, eligible, status in rows:
        cells = [
            str(sequence),
            "교실형안전체험관",
            title,
            operation,
            reception,
            audience,
            eligible,
            "온라인",
            (
                f"<button onclick=\"goViewExprn('{identity}', 'view', this);\">"
                f"{status}</button>"
            ),
        ]
        if unsafe_header:
            cells.append("010-1234-5678")
        body.append("<tr>" + "".join(f"<td>{value}</td>" for value in cells) + "</tr>")
    return f"""
<!doctype html>
<html lang="ko">
<head><title>경상남도교육청 통합예약포털 견학/체험-교실형안전체험관</title></head>
<body>
<form id="exprnListForm" method="post">
  <input name="insttId" value="csec">
  <a class="on" data-seq="csec">교실형안전체험관</a>
  {''.join(
      f'<label>{label}<input name="srchRsvAreaSe" value="{code}"></label>'
      for label, code in collector.TONGYEONG_EXPERIENCE_REGION_FILTER_CODES.items()
  )}
</form>
<div>전체 : {total}건 (1 / {last_page})</div>
<table class="reserv-list-table">
  <thead><tr>{''.join(f'<th>{value}</th>' for value in headers)}</tr></thead>
  <tbody>{''.join(body)}</tbody>
</table>
<form name="pagingForm" method="post" action="/yeyak/exprn/exprnList.do">
  <input name="currPage" value="1">
  <input name="pageIndex" value="{page_size}">
  <input name="limitRowCo" value="{page_size}">
  <input name="maxSn" value="{page_size}">
  <input name="minSn" value="0">
  <input name="limitOffset" value="0">
  <input name="insttId" value="csec">
  <input name="mi" value="14341">
</form>
</body></html>
""".encode("utf-8")


def _target() -> dict[str, str]:
    return {
        "provider": collector.TONGYEONG_EXPERIENCE_PROVIDER,
        "url": collector.TONGYEONG_EXPERIENCE_URL,
    }


def _fetcher_for(
    calls: list[tuple[str, str, dict[str, str]]],
    *,
    bootstrap: bool = True,
    complete_rows: list[tuple[Any, ...]] | None = None,
    first_rows: list[tuple[Any, ...]] | None = None,
    unsafe_header: bool = False,
    mutate_complete_recheck: bool = False,
):
    state = {"first_get": True, "post_count": 0}
    full = complete_rows or _ROWS
    initial = first_rows or full[:10]

    def fetcher(
        _session: Any,
        method: str,
        url: str,
        data: Mapping[str, str],
        _timeout: int,
    ) -> _Response:
        calls.append((method, url, dict(data)))
        if method == "GET" and bootstrap and state["first_get"]:
            state["first_get"] = False
            return _Response(
                url=url,
                status_code=302,
                location=collector.TONGYEONG_EXPERIENCE_BOOTSTRAP_LOCATION,
            )
        if method == "GET":
            return _Response(
                url=url,
                content=_list_html(initial, 10, declared_total=len(full)),
            )
        state["post_count"] += 1
        selected = full
        if mutate_complete_recheck and state["post_count"] == 2:
            selected = [*full]
            changed = list(selected[3])
            changed[5] = changed[5] + " 변경"
            selected[3] = tuple(changed)
        return _Response(
            url=url,
            content=_list_html(
                selected,
                50,
                declared_total=len(full),
                unsafe_header=unsafe_header,
            ),
        )

    return fetcher


def _collect(**fetcher_kwargs: Any):
    calls: list[tuple[str, str, dict[str, str]]] = []
    session = _Session()
    rows, parser, meta = collector.collect_tongyeong_experience(
        _target(),
        today="2026-08-05",
        timeout=3,
        max_pages=1,
        detail_limit=10,
        session_factory=lambda: session,
        fetcher=_fetcher_for(calls, **fetcher_kwargs),
    )
    return rows, parser, meta, calls, session


def test_target_identity_is_exact() -> None:
    assert collector.is_tongyeong_experience_target(_target())
    assert not collector.is_tongyeong_experience_target(
        {**_target(), "provider": "MUNI_SERVICE_GNE_GO_KR_OTHER"}
    )
    assert not collector.is_tongyeong_experience_target(
        {**_target(), "url": collector.TONGYEONG_EXPERIENCE_URL + "&page=2"}
    )


def test_request_boundary_allows_only_exact_public_lists() -> None:
    collector._validate_request("GET", collector.TONGYEONG_EXPERIENCE_URL, {})
    collector._validate_request(
        "POST",
        collector.TONGYEONG_EXPERIENCE_POST_URL,
        collector.TONGYEONG_EXPERIENCE_SAFE_POST_DATA,
    )
    refused = [
        (
            "POST",
            "https://service.gne.go.kr/yeyak/exprn/exprnInfo.do?mi=14341",
            {"exprnEstbsSeq": "10120"},
        ),
        (
            "GET",
            "https://service.gne.go.kr/sso/agentInitProc.jsp",
            {},
        ),
        (
            "GET",
            "https://service.gne.go.kr/yeyak/exprn/apply.do?mi=14341",
            {},
        ),
        (
            "POST",
            collector.TONGYEONG_EXPERIENCE_POST_URL,
            {**collector.TONGYEONG_EXPERIENCE_SAFE_POST_DATA, "pageIndex": "500"},
        ),
    ]
    for method, url, data in refused:
        with pytest.raises(collector.TongyeongExperienceContractError):
            collector._validate_request(method, url, data)


def test_complete_safe_list_snapshot_and_locked_taxonomy() -> None:
    rows, parser, meta, calls, session = _collect()

    assert parser == collector.TONGYEONG_EXPERIENCE_PARSER
    assert len(rows) == 8
    row = next(item for item in rows if item["source_course_id"] == "10120")
    assert row["provider_course_id"] == (
        "MUNI_SERVICE_GNE_GO_KR_8180F18B:experience:10120"
    )
    assert row["municipality_code"] == "4822000000"
    assert row["title"] == "[통영] 충무초 교실형 안전체험관 (안단테 안전체험)"
    assert row["branch"] == "충무초 교실형 안전체험관"
    assert row["service_group"] == "체험"
    assert row["service_group_policy"] == "locked"
    assert row["classification_locked"] is True
    assert row["domain_category"] == "체험·견학"
    assert all(item["application_url"] == "" for item in rows)
    assert all(item["reservation_available"] is False for item in rows)
    assert all(
        bool(item["application_url"]) is item["reservation_available"]
        for item in rows
    )
    assert all(item["address"] == item["venue_address"] == "" for item in rows)
    assert {item["municipality_code"] for item in rows} == {
        "4812000000",
        "4822000000",
        "4825000000",
        "4833000000",
        "4873000000",
        "4887000000",
    }

    assert meta["source_total"] == 12
    assert meta["source_current_count"] == 8
    assert meta["source_expired_count"] == 4
    assert meta["returned_count"] == 8
    assert meta["returned_municipality_count"] == 6
    assert meta["municipality_counts"] == {
        "4812000000": 3,
        "4822000000": 1,
        "4825000000": 1,
        "4833000000": 1,
        "4873000000": 1,
        "4887000000": 1,
    }
    assert meta["uncovered_current_regions"] == []
    assert meta["first_page_row_count"] == 10
    assert meta["complete_page_row_count"] == 12
    assert meta["declared_total"] == 12
    assert meta["declared_first_page_last"] == 2
    assert meta["declared_complete_page_last"] == 1
    assert meta["exact_ordinal_first"] == 12
    assert meta["exact_ordinal_last"] == 1
    assert meta["official_detail_controls_observed_not_called"] == 12
    assert meta["logical_requests"] == 5
    assert len(calls) == 5
    assert calls[0] == calls[1]
    assert meta["sso_bootstrap_locations_observed_not_followed"] == 1
    for key in (
        "unsafe_endpoint_requests",
        "detail_requests",
        "application_endpoint_requests",
        "login_endpoint_requests",
        "identity_endpoint_requests",
        "applicant_endpoint_requests",
        "attachment_endpoint_requests",
        "download_endpoint_requests",
        "pii_endpoint_requests",
    ):
        assert meta[key] == 0
    assert meta["snapshot_complete"] is meta["full_snapshot_validated"] is True
    assert session.closed is True


def test_without_bootstrap_redirect_still_uses_only_four_lists() -> None:
    rows, _, meta, calls, _ = _collect(bootstrap=False)
    assert len(rows) == 8
    assert meta["logical_requests"] == len(calls) == 4
    assert meta["sso_bootstrap_locations_observed_not_followed"] == 0


def test_applicant_pii_column_fails_closed() -> None:
    rows, _, meta, _, session = _collect(unsafe_header=True)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "applicant/PII column" in meta["errors"][0]
    assert session.closed is True


def test_missing_ordinal_fails_closed() -> None:
    incomplete = [*_ROWS]
    incomplete.pop(5)
    rows, _, meta, _, _ = _collect(complete_rows=incomplete)
    assert rows == []
    assert "ordinals do not prove" in meta["errors"][0]


def test_first_page_mismatch_fails_closed() -> None:
    mismatched = [*_ROWS[:10]]
    changed = list(mismatched[0])
    changed[5] = changed[5] + " 변경"
    mismatched[0] = tuple(changed)
    rows, _, meta, _, _ = _collect(first_rows=mismatched)
    assert rows == []
    assert "first page does not match" in meta["errors"][0]


def test_complete_recheck_change_fails_closed() -> None:
    rows, _, meta, _, _ = _collect(mutate_complete_recheck=True)
    assert rows == []
    assert "complete-list boundary changed" in meta["errors"][0]


def test_new_current_region_outside_coverage_fails_closed() -> None:
    changed_rows = [*_ROWS]
    local = list(changed_rows[3])
    local[2] = "[거제] 충무초 교실형 안전체험관"
    changed_rows[3] = tuple(local)
    rows, _, meta, _, _ = _collect(complete_rows=changed_rows)
    assert rows == []
    assert "current region is outside audited coverage: 거제" in meta["errors"][0]


def test_current_identity_region_branch_whitelist_is_exact() -> None:
    changed_rows = [*_ROWS]
    local = list(changed_rows[3])
    local[2] = "[통영] 다른학교 교실형 안전체험관"
    changed_rows[3] = tuple(local)
    rows, _, meta, _, _ = _collect(complete_rows=changed_rows)
    assert rows == []
    assert "identity/region/branch is outside the exact whitelist: 10120" in (
        meta["errors"][0]
    )


def test_dedupe_cannot_silently_remove_complete_output() -> None:
    calls: list[tuple[str, str, dict[str, str]]] = []
    rows, _, meta = collector.collect_tongyeong_experience(
        _target(),
        today="2026-08-05",
        timeout=3,
        max_pages=1,
        detail_limit=10,
        session_factory=_Session,
        fetcher=_fetcher_for(calls),
        dedupe_rows=lambda _rows: [],
    )
    assert rows == []
    assert "dedupe changed complete output" in meta["errors"][0]


def test_exact_dispatch_yaml_operational_coverage_and_ops_scope(monkeypatch) -> None:
    expected = ([{"provider": collector.TONGYEONG_EXPERIENCE_PROVIDER}], "fixture", {})
    captured: dict[str, Any] = {}

    def fake_collect(target: Any, **kwargs: Any):
        captured["target"] = target
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(collector, "collect_gne_csec_experience", fake_collect)
    target = router.CrawlTarget(
        provider=collector.TONGYEONG_EXPERIENCE_PROVIDER,
        name="경남교육청 교실형 안전체험관",
        branch=collector.TONGYEONG_EXPERIENCE_BRANCH,
        url=collector.TONGYEONG_EXPERIENCE_URL,
        source="test",
        priority=1,
        region="경상남도",
        extra={},
    )
    assert router.collect_from_url(
        target,
        timeout=7,
        max_depth=0,
        max_pages=1,
        detail_limit=11,
    ) == expected
    assert captured["target"] is target
    assert captured["timeout"] == 7
    assert captured["max_pages"] == 1
    assert captured["detail_limit"] == 11
    assert callable(captured["session_factory"])

    root = Path(__file__).resolve().parents[1] / "config"
    targets = yaml.safe_load(
        (root / "crawl_targets/municipal_integrated_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )["targets"]
    matches = [
        row
        for row in targets
        if row.get("provider") == collector.TONGYEONG_EXPERIENCE_PROVIDER
    ]
    assert len(matches) == 1
    configured = matches[0]
    assert configured["url"] == collector.TONGYEONG_EXPERIENCE_URL
    assert configured["crawler_status"] == "ready"
    assert configured["ops_scopes"] == ["experience"]
    assert configured["service_group"] == "체험"
    assert configured["ownership"]["canonical_candidate_id"] == (
        collector.TONGYEONG_EXPERIENCE_CANDIDATE_ID
    )

    operational = yaml.safe_load(
        (root / "municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )["entries"]
    operational_matches = [
        row
        for row in operational
        if row.get("provider") == collector.TONGYEONG_EXPERIENCE_PROVIDER
    ]
    assert len(operational_matches) == 1
    assert operational_matches[0]["row_count"] == 8

    coverage = yaml.safe_load(
        (root / "municipal_integrated_reservation_coverage.yaml").read_text(
            encoding="utf-8"
        )
    )["municipalities"]
    covered_codes = {
        "4812000000",
        "4822000000",
        "4825000000",
        "4833000000",
        "4873000000",
        "4887000000",
    }
    for coverage_row in coverage:
        if coverage_row.get("code") not in covered_codes:
            continue
        for key in (
            "owner_providers",
            "promoted_providers",
            "yaml_owner_providers",
        ):
            assert collector.TONGYEONG_EXPERIENCE_PROVIDER in coverage_row[key]

    reference = ops._region_reference()
    names = {
        "경상남도 창원시",
        "경상남도 통영시",
        "경상남도 김해시",
        "경상남도 양산시",
        "경상남도 함안군",
        "경상남도 함양군",
    }
    for name in names:
        assert collector.TONGYEONG_EXPERIENCE_PROVIDER in (
            reference.configured_by_scope["experience"][name]
        )
        assert collector.TONGYEONG_EXPERIENCE_PROVIDER not in (
            reference.configured_by_scope["education"].get(name, ())
        )
    assert collector.TONGYEONG_EXPERIENCE_PROVIDER not in (
        reference.unmapped_configured_by_scope["experience"]
    )


@pytest.mark.skipif(
    os.getenv("TONGYEONG_EXPERIENCE_LIVE") != "1",
    reason="set TONGYEONG_EXPERIENCE_LIVE=1 for the audited public-list probe",
)
def test_live_tongyeong_experience_unsafe_zero() -> None:
    rows, parser, meta = collector.collect_tongyeong_experience(
        _target(),
        today="2026-08-05",
        timeout=30,
        max_pages=1,
        detail_limit=20,
    )
    assert parser == collector.TONGYEONG_EXPERIENCE_PARSER
    assert meta["errors"] == []
    assert meta["source_total"] == 12
    assert meta["source_current_count"] == 8
    assert meta["returned_count"] == len(rows) == 8
    assert meta["returned_municipality_count"] == 6
    assert all(
        row["provider_course_id"].startswith(
            "MUNI_SERVICE_GNE_GO_KR_8180F18B:experience:"
        )
        for row in rows
    )
    assert all(row["application_url"] == "" for row in rows)
    assert all(
        bool(row["application_url"]) is row["reservation_available"]
        for row in rows
    )
    assert meta["unsafe_endpoint_requests"] == 0
    assert meta["detail_requests"] == 0
    assert meta["application_endpoint_requests"] == 0
    assert meta["login_endpoint_requests"] == 0
    assert meta["pii_endpoint_requests"] == 0
    assert meta["snapshot_complete"] is True
