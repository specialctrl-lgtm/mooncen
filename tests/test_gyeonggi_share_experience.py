from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from backend.ops import region_collection as ops
from Crawler import Crawler_MunicipalYaml as router
from Crawler import municipal_gyeonggi_share_experience as collector
from tools.promote_municipal_integrated_reservation_targets import (
    candidate_id,
    normalized_duplicate_url,
    stable_provider,
)


ROOT = Path(__file__).resolve().parents[1]
TARGET = {
    "provider": collector.GYEONGGI_SHARE_EXPERIENCE_PROVIDER,
    "url": collector.GYEONGGI_SHARE_EXPERIENCE_URL,
}


@dataclass(frozen=True)
class _Program:
    identity: str
    title: str
    list_area: str
    institution: str
    subcategory: str
    source_status: str
    apply_period: str
    event_period: str
    fee: str
    target: str
    address: str
    venue: str


def _programs() -> tuple[_Program, ...]:
    rows: list[_Program] = []
    for index in range(13):
        identity = str(90_000 + index)
        if index == 10:
            area = "오산시"
            address = "경기 오산시 부산중앙로 49"
            venue = "오산이음라운지"
            institution = "오산이음라운지"
            title = "오산이음라운지 체험"
        elif index in {11, 12}:
            area = "양평군"
            address = "경기 양평군 청운면 신론로 358"
            venue = "양평 큰삼촌농촌체험마을"
            institution = "경기도농수산진흥원"
            title = "안성 선비마을 농촌체험" if index == 11 else "양평 농촌체험"
        else:
            area = "광주시"
            address = "경기 광주시 곤지암읍 경충대로 725"
            venue = "경기유기농문화체험센터"
            institution = "경기도농수산진흥원"
            title = f"유기농을 지키는 작은 농부들 {index + 1}"
        rows.append(
            _Program(
                identity=identity,
                title=title,
                list_area=area,
                institution=institution,
                subcategory="실내체험" if index < 11 else "실외체험/캠프",
                source_status=(
                    "대기접수" if index == 1 else "접수마감" if index == 2 else "신청가능"
                ),
                apply_period="2099-08-01 09:00 ~ 2099-08-09 12:00",
                event_period="2099-08-10~2099-08-10 / 월 / 10:00~12:00",
                fee="10,000원" if index >= 11 else "무료",
                target="전체",
                address=address,
                venue=venue,
            )
        )
    return tuple(rows)


PROGRAMS = _programs()


def _filters() -> str:
    return """
      <form id="frm">
        <input id="search-category" name="c1" value="32034">
        <input id="searchCategory" name="c3" value="20">
        <input type="radio" id="reservAvailable" value="0" checked="checked">
        <input type="radio" id="all" value="1">
      </form>
    """


def _card(program: _Program) -> str:
    return f"""
    <li>
      <a href="#" onclick="javascript:goNetFunnelSubmit2('eduView','/lecture/view','{program.identity}','32034','20');">
        <div class="service-card">
          <div class="state-div">
            <span class="state free">{program.list_area}</span>
            <span class="state able">직접예약</span>
          </div>
          <div class="txt-div">
            <div class="recom-list-txt01">
              <span>{program.institution}</span><span>{program.subcategory}</span>
            </div>
            <div class="title ellipsis02">{program.title}</div>
            <div class="article-list-body">
              <div class="info-list-box">
                <dl class="info-list"><dt>예약방법</dt><dd>인터넷</dd></dl>
                <dl class="info-list"><dt>선별방법</dt><dd>선착순</dd></dl>
              </div>
              <div class="info-box"><ul><li>{program.fee}</li><li>대기접수 가능</li></ul></div>
            </div>
          </div>
        </div>
      </a>
    </li>
    """


def _pager(page: int) -> str:
    return f"""
      <div class="paging">
        <a class="ico first" href="?curPage=1&amp;eshare=1&amp;c1=32034&amp;c3=20">처음</a>
        <a class="active" href="#none">{page}</a>
        <a class="ico last" href="?curPage=2&amp;eshare=1&amp;c1=32034&amp;c3=20">마지막</a>
      </div>
    """


def _list_html(
    page: int,
    *,
    polluted_sentinel: bool = False,
    changed_first_title: bool = False,
    bootstrap_shell: bool = False,
) -> bytes:
    if bootstrap_shell:
        return (
            "<!doctype html><html><head><meta charset='utf-8'><title>경기공유서비스</title></head>"
            f"<body>{_filters()}<span class='lineR'><em class='tgg1'></em>개의 정보가 검색되었습니다.</span></body></html>"
        ).encode()
    selected = list(PROGRAMS[:12] if page == 1 else PROGRAMS[12:] if page == 2 else ())
    if page == 3 and polluted_sentinel:
        selected = [PROGRAMS[0]]
    if selected and changed_first_title:
        selected[0] = _Program(
            **{**selected[0].__dict__, "title": selected[0].title + " 변경"}
        )
    list_block = ""
    pager = ""
    if selected:
        list_block = f"<div class='service-list'><ul class='service-card-list'>{''.join(_card(row) for row in selected)}</ul></div>"
        pager = _pager(page)
    return f"""
      <!doctype html><html><head><meta charset="utf-8"><title>경기공유서비스</title></head>
      <body>{_filters()}
        <span class="lineR"><em class="tgg1">13</em>개의 정보가 검색되었습니다.</span>
        {list_block}{pager}
      </body></html>
    """.encode()


def _detail_html(
    program: _Program,
    *,
    title_mismatch: bool = False,
    unknown_address: bool = False,
    bad_fields: bool = False,
) -> bytes:
    title = program.title + (" 불일치" if title_mismatch else "")
    address = "경기 미지시 테스트로 1" if unknown_address else program.address
    fields = [
        ("구분", f"체험/견학({program.subcategory})"),
        ("이용대상", program.target),
        ("신청기간", program.apply_period),
        ("교육기간", program.event_period),
        ("요금", program.fee),
    ]
    if bad_fields:
        fields[0] = ("시설안내", fields[0][1])
    field_html = "".join(
        f"<li><span class='lineL'>{label}</span><span class='txt'>{value}</span></li>"
        for label, value in fields
    )
    return f"""
      <!doctype html><html><head><meta charset="utf-8"><title>경기공유서비스</title></head>
      <body>
        <div class="conHeader">
          <div class="content-head-wrap"><div class="title-secondary">{program.subcategory}</div><div class="title-primary">{title}</div></div>
          <div class="conheadWrap"><span class="txt1">{program.list_area}</span><span class="txt2">{program.institution}</span></div>
          <div class="headCon">
            <span class="option-text">{program.source_status}</span>
            <div class="dataBox">
              <div class="dataHead"><p class="tit">{title}</p></div>
              <div class="dataBody"><ul>
                {field_html}
                <li><span class="lineL">교육장소</span><span class="txt has-btn-map">{address} {program.venue}
                  <button class="aLink map" onclick="f_mapPop('{address}', '37.1', '127.1')">지도보기</button>
                </span></li>
              </ul></div>
            </div>
          </div>
        </div>
        <div id="another-list"><dl><dt>신청기간</dt><dd>1900-01-01</dd></dl></div>
        <a href="/lecture/apply?eshare=1&amp;id={program.identity}">신청하기</a>
        <form action="/member/applicant/private"><input name="phone"></form>
      </body></html>
    """.encode()


@dataclass
class _Response:
    url: str
    content: bytes
    status_code: int = 200

    @property
    def history(self) -> tuple[Any, ...]:
        return ()


class _FixtureSession:
    def __init__(
        self,
        *,
        bootstrap: bool = False,
        polluted_sentinel: bool = False,
        unstable_first: bool = False,
        title_mismatch: bool = False,
        unknown_address: bool = False,
        bad_fields: bool = False,
    ) -> None:
        self.bootstrap = bootstrap
        self.polluted_sentinel = polluted_sentinel
        self.unstable_first = unstable_first
        self.title_mismatch = title_mismatch
        self.unknown_address = unknown_address
        self.bad_fields = bad_fields
        self.calls: list[str] = []
        self.first_calls = 0
        self.closed = False

    def get(self, url: str, *, timeout: int, allow_redirects: bool) -> _Response:
        assert timeout == 3
        assert allow_redirects is False
        self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == collector.GYEONGGI_SHARE_EXPERIENCE_LIST_PATH:
            page = int(query.get("curPage", ["1"])[0])
            if page == 1:
                self.first_calls += 1
                if self.bootstrap and self.first_calls == 1:
                    return _Response(url, _list_html(1, bootstrap_shell=True))
            return _Response(
                url,
                _list_html(
                    page,
                    polluted_sentinel=self.polluted_sentinel,
                    changed_first_title=(
                        self.unstable_first and page == 1 and self.first_calls > 1
                    ),
                ),
            )
        identity = query["id"][0]
        program = next(row for row in PROGRAMS if row.identity == identity)
        return _Response(
            url,
            _detail_html(
                program,
                title_mismatch=self.title_mismatch and identity == PROGRAMS[0].identity,
                unknown_address=self.unknown_address and identity == PROGRAMS[0].identity,
                bad_fields=self.bad_fields and identity == PROGRAMS[0].identity,
            ),
        )

    def close(self) -> None:
        self.closed = True


def _collect(session: _FixtureSession | None = None, **kwargs: Any):
    current = session or _FixtureSession()
    rows, parser, meta = collector.collect_gyeonggi_share_experience(
        TARGET,
        timeout=3,
        max_pages=kwargs.pop("max_pages", 10),
        detail_limit=kwargs.pop("detail_limit", 50),
        today="2099-08-05",
        session_factory=lambda: current,
        **kwargs,
    )
    return rows, parser, meta, current


def test_exact_target_stable_ids_and_complete_gyeonggi_allowlist() -> None:
    assert collector.is_target(TARGET)
    assert not collector.is_target({**TARGET, "url": TARGET["url"] + "&curPage=1"})
    assert not collector.is_target({**TARGET, "url": TARGET["url"] + "#fragment"})
    assert collector.GYEONGGI_SHARE_EXPERIENCE_PROVIDER == stable_provider(TARGET["url"])
    assert collector.GYEONGGI_SHARE_EXPERIENCE_CANDIDATE_ID == candidate_id(
        normalized_duplicate_url(TARGET["url"])
    )
    assert len(collector.GYEONGGI_SHARE_EXPERIENCE_MUNICIPALITIES) == 31


def test_request_allowlist_blocks_every_non_public_route_and_method() -> None:
    assert collector._request_kind("GET", collector.gyeonggi_share_experience_list_url(2)) == "list"
    assert collector._request_kind("GET", collector.gyeonggi_share_experience_detail_url("58874")) == "detail"
    unsafe = (
        ("POST", TARGET["url"]),
        ("GET", "https://share.gg.go.kr/lecture/apply?eshare=1&id=58874"),
        ("GET", "https://share.gg.go.kr/login?id=58874"),
        ("GET", "https://share.gg.go.kr/lecture/view?eshare=1&id=58874&download=1"),
        ("GET", "https://share.gg.go.kr/comm/getImage?upperNo=58874"),
    )
    for method, url in unsafe:
        with pytest.raises(collector.GyeonggiShareExperienceContractError):
            collector._request_kind(method, url)


def test_complete_snapshot_uses_only_address_for_region_and_never_application() -> None:
    rows, parser, meta, session = _collect()
    assert parser == collector.GYEONGGI_SHARE_EXPERIENCE_PARSER
    assert len(rows) == 13
    assert meta["source_total"] == meta["source_current_count"] == meta["returned_count"] == 13
    assert meta["page_counts"] == {1: 12, 2: 1}
    assert meta["list_requests"] == 6 and meta["detail_requests"] == 13
    assert meta["sentinel_page"] == 3
    assert meta["title_address_region_anomalies"] == [
        {"identity": "90011", "title_region": "안성시", "venue_region": "양평군"}
    ]
    assert meta["list_detail_address_region_anomaly_count"] == 0
    assert meta["snapshot_complete"] is meta["full_snapshot_validated"] is True
    assert session.closed is True
    anomaly = next(row for row in rows if row["source_course_id"] == "90011")
    assert anomaly["municipality_code"] == "4183000000"
    assert anomaly["sigungu"] == "양평군"
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["provider_course_id"].startswith(collector.GYEONGGI_SHARE_EXPERIENCE_PROVIDER + ":experience:") for row in rows)
    assert all(row["application_url"] == "" and row["reservation_available"] is False for row in rows)
    assert all("1900-01-01" not in str(row) for row in rows)
    assert all(collector._request_kind("GET", url) in {"list", "detail"} for url in session.calls)
    for key in (
        "application_endpoint_requests",
        "netfunnel_submit_calls",
        "login_auth_identity_applicant_member_pii_endpoint_requests",
        "attachment_image_download_endpoint_requests",
        "unsafe_endpoint_calls",
    ):
        assert meta[key] == 0


def test_exact_cookie_bootstrap_shell_is_retried_only_on_same_safe_list() -> None:
    rows, _parser, meta, session = _collect(_FixtureSession(bootstrap=True))
    assert len(rows) == 13
    assert meta["cookie_bootstrap_shell_requests"] == 1
    assert meta["list_requests"] == 7
    assert session.calls[0] == session.calls[1] == TARGET["url"]


@pytest.mark.parametrize(
    ("session", "message"),
    (
        (_FixtureSession(polluted_sentinel=True), "post-last page"),
        (_FixtureSession(unstable_first=True), "first list page changed"),
        (_FixtureSession(title_mismatch=True), "programme identity mismatch"),
        (_FixtureSession(unknown_address=True), "unknown official map municipality"),
        (_FixtureSession(bad_fields=True), "detail field vocabulary changed"),
    ),
)
def test_contract_drift_is_atomic(session: _FixtureSession, message: str) -> None:
    rows, _parser, meta, current = _collect(session)
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False
    assert current.closed is True


def test_collection_limits_are_atomic() -> None:
    rows, _parser, meta, _session = _collect(detail_limit=12)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit truncates" in meta["configured_collection_error"]
    rows, _parser, meta, _session = _collect(max_pages=5)
    assert rows == []
    assert "max_pages permits" in meta["configured_collection_error"]


def test_router_dispatches_only_exact_provider_and_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_collect(target: Any, **kwargs: Any):
        captured["target"] = target
        captured.update(kwargs)
        return ([{"provider": collector.GYEONGGI_SHARE_EXPERIENCE_PROVIDER}], "fixture", {})

    monkeypatch.setattr(collector, "collect_gyeonggi_share_experience", fake_collect)
    target = router.CrawlTarget(
        provider=collector.GYEONGGI_SHARE_EXPERIENCE_PROVIDER,
        name="경기공유서비스 체험·견학",
        branch="경기공유서비스",
        url=collector.GYEONGGI_SHARE_EXPERIENCE_URL,
        source="test",
        priority=1,
        region="경기도",
        extra={},
    )
    rows, parser, meta = router.collect_from_url(
        target, timeout=3, max_depth=0, max_pages=10, detail_limit=50
    )
    assert rows and parser == "fixture" and meta == {}
    assert captured["target"] is target
    assert captured["max_pages"] == 10 and captured["detail_limit"] == 50
    assert callable(captured["session_factory"])


def test_target_operational_and_ops_experience_coverage_are_exact() -> None:
    targets = yaml.safe_load(
        (ROOT / "config/crawl_targets/municipal_integrated_reservation.yaml").read_text(encoding="utf-8")
    )["targets"]
    matches = [row for row in targets if row.get("provider") == collector.GYEONGGI_SHARE_EXPERIENCE_PROVIDER]
    assert len(matches) == 1
    target = matches[0]
    assert target["url"] == collector.GYEONGGI_SHARE_EXPERIENCE_URL
    assert target["crawler_status"] == "ready"
    assert target["ops_scopes"] == ["experience"]
    expected = {row["code"] for row in collector.GYEONGGI_SHARE_EXPERIENCE_COVERED_MUNICIPALITIES}
    assert {row["code"] for row in target["covered_municipalities"]} == expected

    operational = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_operational.yaml").read_text(encoding="utf-8")
    )["entries"]
    assert any(
        row.get("provider") == collector.GYEONGGI_SHARE_EXPERIENCE_PROVIDER
        and row.get("validation_outcome") == "collected"
        and row.get("row_count") == 24
        for row in operational
    )

    reference = ops._region_reference()
    experience = reference.configured_by_scope["experience"]
    education = reference.configured_by_scope["education"]
    for municipality in collector.GYEONGGI_SHARE_EXPERIENCE_COVERED_MUNICIPALITIES:
        name = municipality["full_name"]
        assert collector.GYEONGGI_SHARE_EXPERIENCE_PROVIDER in experience[name]
        assert collector.GYEONGGI_SHARE_EXPERIENCE_PROVIDER not in education.get(name, ())
    assert collector.GYEONGGI_SHARE_EXPERIENCE_PROVIDER not in reference.unmapped_configured_by_scope["experience"]


@pytest.mark.skipif(
    os.getenv("RUN_GYEONGGI_SHARE_EXPERIENCE_LIVE") != "1",
    reason="set RUN_GYEONGGI_SHARE_EXPERIENCE_LIVE=1 for safe official GET-only validation",
)
def test_live_complete_snapshot_has_no_unsafe_calls() -> None:
    rows, parser, meta = collector.collect_gyeonggi_share_experience(
        TARGET,
        timeout=30,
        max_pages=10,
        detail_limit=50,
        today="2026-08-05",
    )
    assert parser == collector.GYEONGGI_SHARE_EXPERIENCE_PARSER
    assert len(rows) == 24
    assert meta["source_total"] == meta["detail_pages"] == meta["returned_count"] == 24
    assert meta["municipality_counts"] == collector.GYEONGGI_SHARE_EXPERIENCE_LIVE_BASELINE["municipality_counts"]
    assert meta["title_address_region_anomaly_count"] == 1
    assert meta["unsafe_endpoint_calls"] == 0
    assert meta["snapshot_complete"] is meta["full_snapshot_validated"] is True
    assert all(not row["application_url"] and row["reservation_available"] is False for row in rows)
