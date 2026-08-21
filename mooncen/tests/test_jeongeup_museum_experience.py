from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
import yaml

from Crawler import municipal_jeongeup
from Crawler import municipal_jeongeup_museum_experience as museum
from tools.promote_municipal_integrated_reservation_targets import (
    candidate_id,
    normalized_duplicate_url,
    stable_provider,
)


ROOT = Path(__file__).resolve().parents[1]
TARGET = {
    "provider": museum.JEONGEUP_MUSEUM_EXPERIENCE_PROVIDER,
    "url": museum.JEONGEUP_MUSEUM_EXPERIENCE_URL,
}


@dataclass(frozen=True)
class SyntheticProgramme:
    identity: str
    title: str
    source_status: str
    status_class: str
    apply_start: str
    apply_end: str
    event_start: str
    event_end: str
    schedule: str
    venue: str
    target: str
    capacity_current: int
    capacity_total: int


def _programmes() -> list[SyntheticProgramme]:
    result: list[SyntheticProgramme] = []
    for index in range(13):
        if index == 0:
            status, status_class = "계획중", "rec01"
            apply_start, apply_end = "2026-08-10", "2026-08-20"
            event_start = event_end = "2026-08-29"
        elif index == 1:
            status, status_class = "온라인 접수중", "rec02"
            apply_start, apply_end = "2026-07-28", "2026-08-06"
            event_start = event_end = "2026-08-08"
        else:
            status, status_class = "행사종료", "rec04"
            apply_start, apply_end = "2026-06-01", "2026-06-10"
            event_start = event_end = "2026-07-01"
        result.append(
            SyntheticProgramme(
                identity=f"RE{9_000_013 - index:07d}",
                title=f"정읍시립박물관 합성 문화체험 {index + 1:02d}",
                source_status=status,
                status_class=status_class,
                apply_start=apply_start,
                apply_end=apply_end,
                event_start=event_start,
                event_end=event_end,
                schedule=f"{10 + index:02d}:00 - {11 + index:02d}:00",
                venue=f"박물관 교육실 {index + 1}",
                target="정읍시민",
                capacity_current=index + 1,
                capacity_total=20,
            )
        )
    return result


def _navigation_href(page: int, *, order_sort: str = "asc") -> str:
    return "/index.jeongeup?" + urlencode(
        (
            ("menuCd", museum.JEONGEUP_MUSEUM_EXPERIENCE_LIST_MENU),
            ("searchCondition", "RE_NAME"),
            ("searchKeyword", ""),
            ("orderField", ""),
            ("orderSort", order_sort),
            ("searchDateGubun", "3"),
            ("startPage", str(page)),
        )
    )


def _detail_href(programme: SyntheticProgramme, page: int) -> str:
    return "/index.jeongeup?" + urlencode(
        (
            ("menuCd", museum.JEONGEUP_MUSEUM_EXPERIENCE_DETAIL_MENU),
            ("reUniqId", programme.identity),
            ("searchCondition", "RE_NAME"),
            ("searchKeyword", ""),
            ("orderField", ""),
            ("orderSort", "asc"),
            ("searchDateGubun", "3"),
            ("startPage", str(page)),
        )
    )


def _row_html(
    programme: SyntheticProgramme,
    requested_page: int,
    *,
    title_suffix: str = "",
) -> str:
    href = _detail_href(programme, requested_page)
    if programme.source_status == "온라인 접수중":
        control = (
            '<a class="possible possible01 blink" '
            f'href="{href}">행사신청</a>'
        )
    else:
        text = "접수대기" if programme.source_status == "계획중" else "접수마감"
        control = f'<button class="possible possible02">{text}</button>'
    return f"""
      <li>
        <dl>
          <dt><a href="{href}">{programme.title}{title_suffix}</a></dt>
          <dd><strong>교육기간</strong>
            {programme.event_start} ~ {programme.event_end}</dd>
          <dd><strong>접수기간</strong>
            {programme.apply_start} ~ {programme.apply_end}</dd>
          <dd><strong>교육장</strong>{programme.venue}</dd>
        </dl>
        <p class="rec {programme.status_class}">{programme.source_status}
          <span>{programme.capacity_current} / {programme.capacity_total}</span>
        </p>
        {control}
      </li>
    """


def _list_html(
    programmes: list[SyntheticProgramme],
    requested_page: int,
    *,
    bad_clamp: bool = False,
    title_suffix: str = "",
) -> str:
    last = 2
    if requested_page > last:
        actual_page = 1 if bad_clamp else last
        current_marker = ""
    else:
        actual_page = requested_page
        current_marker = f'<span class="on">{actual_page}</span>'
    start = (actual_page - 1) * museum.JEONGEUP_MUSEUM_EXPERIENCE_PAGE_SIZE
    page_rows = programmes[
        start : start + museum.JEONGEUP_MUSEUM_EXPERIENCE_PAGE_SIZE
    ]
    body = "".join(
        _row_html(
            programme,
            requested_page,
            title_suffix=title_suffix if index == 0 else "",
        )
        for index, programme in enumerate(page_rows)
    )
    pager = "".join(
        f'<a href="{_navigation_href(page)}">{page}</a>'
        for page in range(1, last + 1)
    )
    return f"""
    <html><head><meta charset="utf-8">
      <title>문화체험 &gt; 정읍시립박물관</title></head>
    <body><div id="content"><h3>정읍시립박물관</h3>
      <form name="listForm" method="get" action="/index.jeongeup">
        <input type="hidden" name="menuCd"
          value="{museum.JEONGEUP_MUSEUM_EXPERIENCE_LIST_MENU}">
        <input type="hidden" name="startPage" value="{requested_page}">
        <input type="hidden" name="searchCondition" value="RE_NAME">
        <input type="hidden" name="orderField" value="">
        <input type="hidden" name="searchDateGubun" value="3">
        <select name="lectureType"><option value="">선택</option></select>
        <ul class="btn_condition">
          <li><button onclick="searchDatefunc('3')">전체</button></li>
          <li><button onclick="searchDatefunc('1')">접수중</button></li>
        </ul>
        <input name="searchKeyword" value="">
      </form>
      <ul class="search_result">
        <li>모집중 1건</li><li>마감 12건</li>
        <li>검색된 결과 13건</li>
      </ul>
      <div class="bbs_list01"><ul>{body}</ul></div>
      <div class="bbs_page">{current_marker}{pager}</div>
    </div></body></html>
    """


def _detail_html(
    programme: SyntheticProgramme,
    page: int,
    *,
    title_suffix: str = "",
    control_drift: bool = False,
) -> str:
    if programme.source_status == "온라인 접수중" and not control_drift:
        control = '<button type="button" onclick="writeFunc();">신청하기</button>'
    else:
        text = "접수대기" if programme.source_status == "계획중" else "접수마감"
        control = f"<button>{text}</button>"
    return f"""
    <html><head><meta charset="utf-8">
      <title>문화체험 &gt; 정읍시립박물관 &gt; 신청하기</title></head>
    <body><div class="edu_view01">
      <h4>{programme.title}{title_suffix}</h4>
      <table class="view_table"><tbody>
        <tr><th>접수기간</th><td>{programme.apply_start} ~ {programme.apply_end}</td></tr>
        <tr><th>행사기간</th><td>{programme.event_start} ~ {programme.event_end}</td></tr>
        <tr><th>행사시간</th><td>{programme.schedule}</td></tr>
        <tr><th>행사장</th><td>{programme.venue}</td></tr>
        <tr><th>강사명</th><td>저장 금지 개인강사</td></tr>
        <tr><th>수강료/재료비</th><td>무료</td></tr>
        <tr><th>행사대상</th><td>{programme.target}</td></tr>
        <tr><th>신청/정원</th><td>{programme.capacity_current} / {programme.capacity_total}</td></tr>
        <tr><th>문의담당자</th><td>저장 금지 담당자</td></tr>
        <tr><th>문의전화</th><td>063-539-0000</td></tr>
        <tr><th>행사내용</th><td>저장 금지 자유 본문</td></tr>
        <tr><th>강의자료</th><td>
          <a href="/common/download.do?file=private">비공개 첨부</a></td></tr>
        <tr><th>접수상태</th><td>{programme.source_status}</td></tr>
      </tbody></table>
      <div class="btn">
        <p class="btn_apply">{control}</p>
        <p class="btn_back"><a href="{_navigation_href(page)}">목록</a></p>
      </div>
      <form method="post"
        action="/user/jeongeupEpr/traineeWriteAct.jeongeup">
        <input name="applicantName"><input name="applicantPhone">
      </form>
    </div></body></html>
    """


class FakeResponse:
    def __init__(self, url: str, html: str) -> None:
        self.url = url
        self.content = html.encode("utf-8")
        self.text = html
        self.status_code = 200
        self.history: list[Any] = []
        self.headers = {"Content-Type": "text/html; charset=utf-8"}


class FixtureSession:
    def __init__(
        self,
        *,
        bad_clamp: bool = False,
        unstable_first: bool = False,
        duplicate_identity: bool = False,
        detail_mismatch: str = "",
        control_drift: str = "",
    ) -> None:
        self.programmes = _programmes()
        if duplicate_identity:
            self.programmes[-1] = replace(
                self.programmes[-1], identity=self.programmes[-2].identity
            )
        self.bad_clamp = bad_clamp
        self.unstable_first = unstable_first
        self.detail_mismatch = detail_mismatch
        self.control_drift = control_drift
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.list_counts: Counter[int] = Counter()
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        assert parsed.scheme == "https"
        assert parsed.netloc == museum.JEONGEUP_MUSEUM_EXPERIENCE_HOST
        assert parsed.path == museum.JEONGEUP_MUSEUM_EXPERIENCE_PATH
        menu = query.get("menuCd", [""])[0]
        if menu == museum.JEONGEUP_MUSEUM_EXPERIENCE_LIST_MENU:
            requested_page = int(query.get("startPage", ["1"])[0])
            self.list_counts[requested_page] += 1
            suffix = (
                " 변경"
                if self.unstable_first
                and requested_page == 1
                and self.list_counts[requested_page] > 1
                else ""
            )
            return FakeResponse(
                url,
                _list_html(
                    self.programmes,
                    requested_page,
                    bad_clamp=self.bad_clamp and requested_page == 3,
                    title_suffix=suffix,
                ),
            )
        if menu == museum.JEONGEUP_MUSEUM_EXPERIENCE_DETAIL_MENU:
            identity = query.get("reUniqId", [""])[0]
            programme = next(
                item for item in self.programmes if item.identity == identity
            )
            page = self.programmes.index(programme) // 10 + 1
            return FakeResponse(
                url,
                _detail_html(
                    programme,
                    page,
                    title_suffix=" 불일치"
                    if identity == self.detail_mismatch
                    else "",
                    control_drift=identity == self.control_drift,
                ),
            )
        raise AssertionError(f"unsafe or unexpected network request: {url}")

    def close(self) -> None:
        self.closed = True


def _collect(session: FixtureSession, **kwargs: Any):
    return museum.collect(
        TARGET,
        today="2026-08-05",
        timeout=10,
        max_pages=4,
        detail_limit=20,
        session_factory=lambda: session,
        **kwargs,
    )


def test_exact_target_and_stable_repository_identities() -> None:
    assert museum.is_target(TARGET)
    assert not museum.is_target({**TARGET, "url": TARGET["url"] + "&startPage=1"})
    assert not museum.is_target({**TARGET, "url": TARGET["url"] + "#fragment"})
    assert not museum.is_target({**TARGET, "provider": municipal_jeongeup.JEONGEUP_PROVIDER})
    assert museum.JEONGEUP_MUSEUM_EXPERIENCE_PROVIDER == stable_provider(
        museum.JEONGEUP_MUSEUM_EXPERIENCE_URL
    )
    assert museum.JEONGEUP_MUSEUM_EXPERIENCE_CANDIDATE_ID == candidate_id(
        normalized_duplicate_url(museum.JEONGEUP_MUSEUM_EXPERIENCE_URL)
    )
    assert not municipal_jeongeup.is_jeongeup_education_target(TARGET)


def test_production_collection_requires_managed_session() -> None:
    rows, parser, meta = museum.collect(TARGET, today="2026-08-05")
    assert rows == []
    assert parser == museum.JEONGEUP_MUSEUM_EXPERIENCE_PARSER
    assert meta["snapshot_complete"] is False
    assert "session_factory" in meta["configured_collection_error"]


class NeverSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, **_: Any) -> Any:
        self.calls.append(url)
        raise AssertionError("network must not be reached")

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    "url",
    (
        "https://www.jeongeup.go.kr/user/jeongeupEpr/traineeWriteAct.jeongeup",
        "https://www.jeongeup.go.kr/reserve/login.jeongeup",
        "https://www.jeongeup.go.kr/reserve/member/applicant.do",
        "https://www.jeongeup.go.kr/common/download.do?file=private",
        museum.JEONGEUP_MUSEUM_EXPERIENCE_URL + "&searchKeyword=name",
        museum.JEONGEUP_MUSEUM_EXPERIENCE_URL + "#fragment",
    ),
)
def test_runner_refuses_unsafe_endpoints_before_network(url: str) -> None:
    session = NeverSession()
    with museum._Runner(lambda: session, 10) as runner:
        with pytest.raises(museum.JeongeupMuseumExperienceContractError):
            runner.soup(url)
    assert session.calls == []


def test_request_guard_refuses_post_before_network() -> None:
    with pytest.raises(museum.JeongeupMuseumExperienceContractError, match="POST"):
        museum._request_kind("POST", museum.JEONGEUP_MUSEUM_EXPERIENCE_URL)


def test_fixture_complete_snapshot_clamp_details_taxonomy_and_privacy() -> None:
    session = FixtureSession()
    rows, parser, meta = _collect(session)

    assert parser == museum.JEONGEUP_MUSEUM_EXPERIENCE_PARSER
    assert meta["configured_collection_error"] == ""
    assert len(rows) == 2
    assert meta["source_total"] == 13
    assert meta["source_rows"] == 13
    assert meta["data_pages"] == 2
    assert meta["page_counts"] == [10, 3]
    assert meta["post_last_page"] == 3
    assert meta["current_count"] == 2
    assert meta["expired_count"] == 11
    assert meta["source_status_counts"] == {
        "계획중": 1,
        "온라인 접수중": 1,
        "행사종료": 11,
    }
    assert meta["status_counts"] == {"SCHEDULED": 1, "OPEN": 1}
    assert meta["list_requests"] == 6
    assert meta["detail_pages"] == 2
    assert meta["physical_requests"] == 8
    assert meta["boundary_rechecks"] == 3
    assert meta["application_controls_current"] == 1
    assert meta["post_last_clamp_verified"] is True
    assert meta["stable_first_last_overflow"] is True
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["application_controls_executed"] == 0
    assert meta["application_endpoint_requests"] == 0
    assert meta["reservation_lookup_endpoint_requests"] == 0
    assert meta["login_auth_member_applicant_pii_endpoint_requests"] == 0
    assert meta["attachment_download_endpoint_requests"] == 0
    assert meta["post_requests"] == 0
    assert meta["unsafe_endpoint_calls"] == 0
    assert meta["sibling_pages_requested"] == 0
    assert session.closed is True
    assert len(session.calls) == 8

    assert [row["status"] for row in rows] == ["OPEN", "SCHEDULED"]
    for row in rows:
        assert row["service_group"] == "체험"
        assert row["domain_category"] == "체험·견학"
        assert row["program_type"] == "체험"
        assert row["classification_locked"] is True
        assert row["municipality_code"] == "5218000000"
        assert row["description"] == row["title"]
        assert "063-539-0000" not in repr(row)
        assert "저장 금지 개인강사" not in repr(row)
        assert row["raw_fields"]["application_control_executed"] is False
        assert row["raw_fields"]["application_endpoint_fetched"] is False

    called_urls = [url for url, _ in session.calls]
    assert all("traineeWriteAct" not in url for url in called_urls)
    assert all("download" not in url.casefold() for url in called_urls)
    assert all("login" not in url.casefold() for url in called_urls)
    assert all(kwargs["allow_redirects"] is False for _, kwargs in session.calls)
    assert all(museum._request_kind("GET", url) in {"list", "detail"} for url in called_urls)


@pytest.mark.parametrize(
    ("session", "message"),
    (
        (FixtureSession(bad_clamp=True), "did not clamp exactly"),
        (FixtureSession(unstable_first=True), "changed during collection"),
        (FixtureSession(duplicate_identity=True), "duplicated"),
        (
            FixtureSession(detail_mismatch="RE9000013"),
            "list/detail identity drift",
        ),
        (FixtureSession(control_drift="RE9000012"), "open inline control drift"),
    ),
)
def test_contract_drift_fails_the_atomic_snapshot(
    session: FixtureSession, message: str
) -> None:
    rows, _, meta = _collect(session)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert message in meta["configured_collection_error"]


def test_dedupe_cannot_remove_a_current_identity() -> None:
    rows, _, meta = _collect(FixtureSession(), dedupe_rows=lambda rows: rows[:-1])
    assert rows == []
    assert "dedupe changed complete" in meta["configured_collection_error"]


def test_exact_dispatch_injects_managed_session_and_dedupe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from Crawler import Crawler_MunicipalYaml as router

    captured: dict[str, Any] = {}

    def collect(*_args: Any, **kwargs: Any) -> tuple[list[Any], str, dict[str, Any]]:
        captured.update(kwargs)
        return [], "jeongeup-museum-experience", {"snapshot_complete": True}

    monkeypatch.setattr(museum, "collect_jeongeup_museum_experience", collect)
    target = router.CrawlTarget(
        provider=museum.JEONGEUP_MUSEUM_EXPERIENCE_PROVIDER,
        name="정읍시립박물관 문화체험",
        branch=museum.JEONGEUP_MUSEUM_EXPERIENCE_BRANCH,
        url=museum.JEONGEUP_MUSEUM_EXPERIENCE_URL,
        source="test",
    )
    router.collect_from_url(
        target,
        timeout=1,
        max_depth=0,
        max_pages=4,
        detail_limit=20,
    )

    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])
    assert "allow_raw_requests_for_tests" not in captured


def test_target_operational_override_coverage_and_exact_indices() -> None:
    target_document = yaml.safe_load(
        (ROOT / "config/crawl_targets/municipal_integrated_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )
    targets = target_document["targets"]
    matches = [
        item
        for item in targets
        if item.get("provider") == museum.JEONGEUP_MUSEUM_EXPERIENCE_PROVIDER
    ]
    assert len(matches) == 1
    target = matches[0]
    target_index = targets.index(target) + 1
    assert target["url"] == museum.JEONGEUP_MUSEUM_EXPERIENCE_URL
    assert target["crawler_module"] == "Crawler.municipal_jeongeup_museum_experience"
    assert target["crawler_callable"] == "collect_jeongeup_museum_experience"
    assert target["crawler_status"] == "ready"
    assert target["ops_scopes"] == ["experience"]
    assert target["service_group"] == "체험"
    assert target["full_snapshot_required"] is True
    assert target["last_quality"]["collected"] == 7
    assert target["last_quality"]["unsafe_endpoint_calls"] == 0

    operational = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )["entries"]
    entries = [
        item
        for item in operational
        if item.get("provider") == museum.JEONGEUP_MUSEUM_EXPERIENCE_PROVIDER
    ]
    assert len(entries) == 1
    assert entries[0]["target_url"] == museum.JEONGEUP_MUSEUM_EXPERIENCE_URL
    assert entries[0]["validation_outcome"] == "collected"
    assert entries[0]["row_count"] == 7

    overrides = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_overrides.yaml").read_text(
            encoding="utf-8"
        )
    )["municipalities"]
    override = next(item for item in overrides if item.get("code") == "5218000000")
    candidate = next(
        item
        for item in override["candidates"]
        if item.get("provider") == museum.JEONGEUP_MUSEUM_EXPERIENCE_PROVIDER
    )
    assert candidate["status"] == "candidate"
    assert candidate["score"] == 100

    coverage = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_coverage.yaml").read_text(
            encoding="utf-8"
        )
    )["municipalities"]
    municipality = next(item for item in coverage if item.get("code") == "5218000000")
    for key in ("owner_providers", "promoted_providers", "yaml_owner_providers"):
        assert museum.JEONGEUP_MUSEUM_EXPERIENCE_PROVIDER in municipality[key]
    exact = next(
        item
        for item in municipality["evidence"]
        if item.get("kind") == "exact_active_url"
        and item.get("provider") == museum.JEONGEUP_MUSEUM_EXPERIENCE_PROVIDER
    )
    assert exact["target_url"] == museum.JEONGEUP_MUSEUM_EXPERIENCE_URL
    assert exact["target_index"] == target_index

    gunsan_provider = "MUNI_GREEN_GUNSAN_GO_KR_3031CB82"
    gunsan_index = next(
        index
        for index, item in enumerate(targets, 1)
        if item.get("provider") == gunsan_provider
    )
    gunsan_municipality = next(item for item in coverage if item.get("code") == "5213000000")
    gunsan_exact = next(
        item
        for item in gunsan_municipality["evidence"]
        if item.get("kind") == "exact_active_url"
        and item.get("provider") == gunsan_provider
    )
    assert gunsan_exact["target_index"] == gunsan_index


def test_ops_region_reference_maps_the_new_experience_provider() -> None:
    from backend.ops import region_collection

    region_collection._REFERENCE_SIGNATURE = None
    region_collection._REFERENCE_VALUE = None
    reference = region_collection._region_reference()
    municipality = museum.JEONGEUP_MUSEUM_EXPERIENCE_MUNICIPALITY_NAME
    provider = museum.JEONGEUP_MUSEUM_EXPERIENCE_PROVIDER
    assert provider in reference.configured_by_municipality[municipality]
    assert provider in reference.configured_by_scope["experience"][municipality]
    assert reference.provider_municipalities[provider] == {municipality}
    assert provider not in reference.unmapped_configured_providers


def test_live_baseline_and_sibling_audit_are_explicit() -> None:
    assert museum.JEONGEUP_MUSEUM_EXPERIENCE_LIVE_BASELINE == {
        "checked_at": "2026-08-05",
        "source_total": 58,
        "data_pages": 6,
        "current_count": 7,
        "expired_count": 51,
        "source_status_counts": {
            "계획중": 1,
            "온라인 접수중": 6,
            "행사종료": 51,
        },
        "current_status_counts": {"SCHEDULED": 1, "OPEN": 6},
        "application_controls_current": 6,
    }
    audit = museum.JEONGEUP_MUSEUM_EXPERIENCE_SIBLING_AUDIT
    assert audit["art_museum_family"]["observed_current"] == 4
    assert audit["toy_rental"]["decision"] == "exclude_equipment_rental_calendar"
    assert "facility_directory" in audit["tour_guide"]["decision"]


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_JEONGEUP_MUSEUM_EXPERIENCE") != "1",
    reason="set RUN_LIVE_JEONGEUP_MUSEUM_EXPERIENCE=1 for audited public GETs",
)
def test_live_snapshot_matches_audited_baseline_without_unsafe_calls() -> None:
    rows, parser, meta = museum.collect(
        TARGET,
        today="2026-08-05",
        timeout=20,
        max_pages=10,
        detail_limit=20,
        session_factory=museum._default_session_factory,
    )
    assert parser == museum.JEONGEUP_MUSEUM_EXPERIENCE_PARSER
    assert meta["configured_collection_error"] == ""
    assert len(rows) == 7
    assert meta["source_total"] == 58
    assert meta["data_pages"] == 6
    assert meta["page_counts"] == [10, 10, 10, 10, 10, 8]
    assert meta["post_last_page"] == 7
    assert meta["current_count"] == 7
    assert meta["expired_count"] == 51
    assert meta["status_counts"] == {"OPEN": 6, "SCHEDULED": 1}
    assert meta["detail_pages"] == 7
    assert meta["physical_requests"] == 17
    for key in (
        "application_controls_executed",
        "application_endpoint_requests",
        "reservation_lookup_endpoint_requests",
        "login_auth_member_applicant_pii_endpoint_requests",
        "attachment_download_endpoint_requests",
        "post_requests",
        "unsafe_endpoint_calls",
        "sibling_pages_requested",
        "privacy_violations",
    ):
        assert meta[key] == 0
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["classification_locked"] is True for row in rows)
