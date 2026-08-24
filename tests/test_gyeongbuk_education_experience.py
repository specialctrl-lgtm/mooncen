from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

import pytest
import yaml

from Crawler import municipal_gyeongbuk_education_experience as gbe
from tools.promote_municipal_integrated_reservation_targets import (
    candidate_id,
    normalized_duplicate_url,
    stable_provider,
)


ROOT = Path(__file__).resolve().parents[1]


def _target(**updates: str) -> dict[str, str]:
    target = {
        "provider": gbe.GYEONGBUK_EDU_EXPERIENCE_PROVIDER,
        "url": gbe.GYEONGBUK_EDU_EXPERIENCE_URL,
    }
    target.update(updates)
    return target


_ROWS: list[dict[str, Any]] = [
    {
        "ordinal": 12,
        "institution": "상주수학체험센터",
        "primary": "가족 수학놀이터",
        "secondary": "트러스 한지등",
        "start": "2026/08/06",
        "end": "2026/08/06",
        "apply_start": "2026/07/15 09:00:00",
        "apply_end": "2026/08/05 17:00:59",
        "status": "접수중",
        "seq": "228",
        "period": "1252",
        "system": "sjmath",
    },
    {
        "ordinal": 11,
        "institution": "경주안전체험관",
        "primary": "가족안전체험",
        "secondary": "토요 체험",
        "start": "2026/08/22",
        "end": "2026/08/22",
        "apply_start": "2026/08/12 20:00:00",
        "apply_end": "2026/08/19 23:59:59",
        "status": "예정",
        "seq": "366",
        "period": "1071",
        "system": "gjsafe",
    },
    {
        "ordinal": 10,
        "institution": "경상북도교육청 수학문화관",
        "primary": "수학과 함께 하는 가족 캠프",
        "secondary": "정폭도형 만들기",
        "start": "2026/08/08",
        "end": "2026/08/08",
        "apply_start": "2026/07/15 10:00:00",
        "apply_end": "2026/07/22 23:59:59",
        "status": "마감",
        "seq": "431",
        "period": "1203",
        "system": "gbemc",
    },
    {
        "ordinal": 9,
        "institution": "상주수학체험센터",
        "primary": "찾아가는 수학체험교실",
        "secondary": "신청학교 운영",
        "start": "2026/09/09",
        "end": "2026/09/10",
        "apply_start": "2026/04/07 08:00:00",
        "apply_end": "2026/04/10 17:00:59",
        "status": "마감",
        "seq": "75",
        "period": "1167",
        "system": "sjmath",
    },
    {
        "ordinal": 8,
        "institution": "테스트홈페이지",
        "primary": "테스트",
        "secondary": "테스트",
        "start": "2026/11/01",
        "end": "2026/11/01",
        "apply_start": "2025/10/31 09:00:00",
        "apply_end": "2026/08/31 23:59:59",
        "status": "접수중",
        "seq": "360",
        "period": "977",
        "system": "klic",
    },
] + [
    {
        "ordinal": ordinal,
        "institution": "김천오토캠핑장",
        "primary": "지난 가족 체험",
        "secondary": f"지난 회차 {ordinal}",
        "start": "2025/05/01",
        "end": "2025/05/02",
        "apply_start": "2025/04/01 09:00:00",
        "apply_end": "2025/04/20 17:00:59",
        "status": "마감",
        "seq": str(500 + ordinal),
        "period": str(700 + ordinal),
        "system": "gccamping",
    }
    for ordinal in range(7, 0, -1)
]


def _title(row: dict[str, Any]) -> str:
    return (
        f"{row['primary']} - {row['secondary']}"
        if row["secondary"]
        else row["primary"]
    )


def _list_row(row: dict[str, Any]) -> str:
    attrs = (
        f'data-id="{row["seq"]}" data-period-id="{row["period"]}" '
        f'data-rssysid="{row["system"]}"'
    )
    return f"""
      <tr>
        <td class="mobRemove">{row['ordinal']}</td>
        <td><p class="tit">기관명</p>{row['institution']}</td>
        <td class="al"><p class="tit">체험명</p>
          <a class="viewExprnInfo" href="javascript:" {attrs}>
            <span class="pc_mint">{row['primary']}</span>
            <ul class="list_st2"><li>{row['secondary']}</li></ul>
          </a>
        </td>
        <td><p class="tit">운영기간</p><p>{row['start']} ~</p><p>{row['end']}</p></td>
        <td><p class="tit">접수기간</p><p>{row['apply_start']} ~</p><p>{row['apply_end']}</p></td>
        <td><p class="tit">체험대상</p>학생<br/>학생 및 가족</td>
        <td><p class="tit">신청대상</p>회원전체</td>
        <td class="ac"><p class="tit">예약상태</p>
          <a class="viewExprnInfo" href="javascript:" {attrs}>{row['status']}</a>
        </td>
      </tr>
    """


def _list_page(
    page: int,
    *,
    unstable_first: bool = False,
    wrong_sentinel: bool = False,
) -> str:
    if page == 1:
        rows = deepcopy(_ROWS[:10])
        if unstable_first:
            rows[0]["status"] = "마감"
    elif page == 2:
        rows = deepcopy(_ROWS[10:])
    else:
        rows = []
    if not rows:
        body = (
            _list_row(_ROWS[-1])
            if wrong_sentinel
            else '<tr><td class="noData" colspan="8">등록된 체험이 없습니다.</td></tr>'
        )
    else:
        body = "".join(_list_row(row) for row in rows)
    return f"""
      <html><head><title>경상북도통합예약시스템-견학/체험</title></head><body>
      <h2 class="titleH2">견학/체험</h2>
      <h3 class="titT1">전체 12 건 ( {page} /2)</h3>
      <table><thead><tr>
        <th>순번</th><th>기관명</th><th>체험명</th><th>운영기간</th>
        <th>접수기간</th><th>체험대상</th><th>신청대상</th><th>예약상태</th>
      </tr></thead><tbody>{body}</tbody></table>
      </body></html>
    """


def _detail_page(row: dict[str, Any], *, title_drift: bool = False) -> str:
    title = "변경된 제목" if title_drift else _title(row)
    return f"""
      <html><head><title>경상북도통합예약시스템-견학/체험</title></head><body>
      <h2 class="titleH2">견학/체험</h2>
      <form id="exprnInfoForm" method="get">
        <input name="mi" value="17609"/>
        <input name="rsSysId" value="{row['system']}"/>
        <input name="exprnSeq" value="{row['seq']}"/>
        <input name="exprnPeriodSeq" value="{row['period']}"/>
        <input name="exprnScheSeq" value=""/>
        <input name="currPage" value="{1 if row['ordinal'] >= 3 else 2}"/>
      </form>
      <div class="content_box rveInfo"><h4 class="titT2">{title}</h4><ul>
        <li><span>운영기관</span>{row['institution']}</li>
        <li><span>운영기간</span>{row['start']} ~ {row['end']} (목)</li>
        <li><span>접수기간</span>{row['apply_start']} ~ {row['apply_end']}</li>
        <li><span>신청대상</span>회원전체</li>
        <li><span>대상</span>학생 및 가족</li>
        <li><span>예약지역</span>포항시, 경주시, 상주시</li>
      </ul></div>
      <div class="cnDivExprnDetailCn">공개 이용안내</div>
      <div class="cnDivExprnCn">공개 체험안내</div>
      <div class="cnDivExprnAtpn">공개 유의사항</div>
      <script>/* exprnReqstPage.do is inert script text and is never fetched */</script>
      </body></html>
    """


class _Response:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.content = text.encode("utf-8")
        self.text = text
        self.status_code = 200
        self.history: list[Any] = []
        self.headers = {"Content-Type": "text/html; charset=UTF-8"}


class _FixtureSession:
    def __init__(
        self,
        *,
        unstable_first: bool = False,
        wrong_sentinel: bool = False,
        detail_title_drift: bool = False,
    ) -> None:
        self.headers: dict[str, str] = {}
        self.unstable_first = unstable_first
        self.wrong_sentinel = wrong_sentinel
        self.detail_title_drift = detail_title_drift
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.list_gets = 0
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append(("GET", url, kwargs))
        if url == gbe.GYEONGBUK_EDU_EXPERIENCE_URL:
            self.list_gets += 1
            return _Response(
                url,
                _list_page(
                    1,
                    unstable_first=self.unstable_first and self.list_gets > 1,
                ),
            )
        query = dict(parse_qsl(urlparse(url).query))
        row = next(
            item
            for item in _ROWS
            if item["seq"] == query["exprnSeq"]
            and item["period"] == query["exprnPeriodSeq"]
        )
        return _Response(
            url,
            _detail_page(
                row,
                title_drift=self.detail_title_drift and row["seq"] == "228",
            ),
        )

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append(("POST", url, kwargs))
        assert url == gbe.GYEONGBUK_EDU_EXPERIENCE_POST_URL
        data = kwargs["data"]
        assert data == dict(gbe._paging_form(int(data["currPage"])))
        page = int(data["currPage"])
        return _Response(
            url,
            _list_page(
                page,
                wrong_sentinel=self.wrong_sentinel and page == 3,
            ),
        )

    def close(self) -> None:
        self.closed = True


def _collect(session: _FixtureSession, **kwargs: Any):
    return gbe.collect(
        _target(),
        today="2026-08-05",
        max_pages=10,
        detail_limit=20,
        timeout=10,
        session_factory=lambda: session,
        **kwargs,
    )


def test_exact_target_and_stable_repository_ids() -> None:
    assert gbe.is_target(_target())
    assert not gbe.is_target(
        _target(url=gbe.GYEONGBUK_EDU_EXPERIENCE_URL + "&currPage=1")
    )
    assert not gbe.is_target(
        _target(url=gbe.GYEONGBUK_EDU_EXPERIENCE_URL.replace("https://", "http://"))
    )
    assert gbe.GYEONGBUK_EDU_EXPERIENCE_PROVIDER == stable_provider(
        gbe.GYEONGBUK_EDU_EXPERIENCE_URL
    )
    assert gbe.GYEONGBUK_EDU_EXPERIENCE_CANDIDATE_ID == candidate_id(
        normalized_duplicate_url(gbe.GYEONGBUK_EDU_EXPERIENCE_URL)
    )


def test_production_collection_requires_managed_session() -> None:
    rows, parser, meta = gbe.collect(_target(), today="2026-08-05")
    assert rows == []
    assert parser == gbe.GYEONGBUK_EDU_EXPERIENCE_PARSER
    assert meta["snapshot_complete"] is False
    assert "session_factory" in meta["configured_collection_error"]


class _NeverSession:
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
        "https://www.gbe.kr/edushare/exprn/exprnReqstPage.do?mi=17609",
        "https://www.gbe.kr/edushare/lo/login/loginCertiPage.do",
        "https://www.gbe.kr/edushare/member/applicant.do",
        "https://www.gbe.kr/common/fileDownload.do?fileKey=1",
        gbe.GYEONGBUK_EDU_EXPERIENCE_URL,
    ),
)
def test_runner_refuses_unsafe_or_non_detail_get_before_network(url: str) -> None:
    session = _NeverSession()
    with gbe._Runner(lambda: session, 10) as runner:
        with pytest.raises(
            gbe.GyeongbukEducationExperienceContractError,
            match="route refused",
        ):
            runner.detail_get(url)
    assert session.calls == []


def test_fixture_proves_complete_safe_snapshot_and_conservative_venue_mapping() -> None:
    session = _FixtureSession()
    rows, parser, meta = _collect(session)

    assert parser == gbe.GYEONGBUK_EDU_EXPERIENCE_PARSER
    assert len(rows) == 3
    assert meta["source_total"] == 12
    assert meta["source_rows"] == 12
    assert meta["source_current_count"] == 5
    assert meta["source_expired_count"] == 7
    assert meta["returned_count"] == 3
    assert meta["excluded_nonproduction_count"] == 1
    assert meta["excluded_variable_venue_count"] == 1
    assert meta["unmapped_unknown_institution_count"] == 0
    assert meta["data_pages"] == 2
    assert meta["sentinel_page"] == 3
    assert meta["page_counts"] == {1: 10, 2: 2}
    assert meta["list_requests"] == 6
    assert meta["get_requests"] == 7
    assert meta["post_requests"] == 4
    assert meta["detail_requests"] == 5
    assert meta["detail_pages"] == 5
    assert meta["physical_requests"] == 11
    assert meta["stable_first_page"] is True
    assert meta["stable_last_page"] is True
    assert meta["stable_sentinel_page"] is True
    assert meta["reservation_region_field_used_for_venue"] is False
    assert meta["application_endpoint_requests"] == 0
    assert meta["login_auth_identity_applicant_member_pii_endpoint_requests"] == 0
    assert meta["calendar_endpoint_requests"] == 0
    assert meta["attachment_download_endpoint_requests"] == 0
    assert meta["unsafe_endpoint_calls"] == 0
    assert meta["snapshot_complete"] is True
    assert session.closed is True

    assert [row["municipality_code"] for row in rows] == [
        "4725000000",
        "4713000000",
        "4711000000",
    ]
    assert rows[0]["provider_course_id"] == (
        f"{gbe.GYEONGBUK_EDU_EXPERIENCE_PROVIDER}:experience:228:1252"
    )
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["classification_locked"] is True for row in rows)
    assert all(
        bool(row["application_url"]) == row["reservation_available"]
        for row in rows
    )
    assert rows[0]["application_url"] == rows[0]["raw_url"]
    assert rows[1]["application_url"] == ""
    assert rows[2]["application_url"] == ""
    assert all("exprnReqstPage" not in row["application_url"] for row in rows)
    assert all("예약지역" not in row["raw_fields"] for row in rows)

    requested = [url for _, url, _ in session.calls]
    assert not any("exprnReqstPage" in url for url in requested)
    assert not any("login" in url.lower() for url in requested)
    assert not any("fileDownload" in url for url in requested)


@pytest.mark.parametrize(
    ("session", "message"),
    (
        (_FixtureSession(unstable_first=True), "first page changed"),
        (_FixtureSession(wrong_sentinel=True), "post-last page is not exactly empty"),
        (_FixtureSession(detail_title_drift=True), "detail/list title mismatch"),
    ),
)
def test_contract_drift_fails_the_atomic_snapshot(
    session: _FixtureSession, message: str
) -> None:
    rows, _, meta = _collect(session)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert message in meta["configured_collection_error"]


def test_detail_limit_and_list_limit_fail_closed() -> None:
    rows, _, meta = gbe.collect(
        _target(),
        today="2026-08-05",
        max_pages=5,
        detail_limit=20,
        session_factory=lambda: _FixtureSession(),
    )
    assert rows == []
    assert "max_pages permits 5 of 6" in meta["configured_collection_error"]

    rows, _, meta = gbe.collect(
        _target(),
        today="2026-08-05",
        max_pages=10,
        detail_limit=4,
        session_factory=lambda: _FixtureSession(),
    )
    assert rows == []
    assert "detail_limit permits 4 of 5" in meta["configured_collection_error"]


def test_dispatch_injects_managed_session_and_dedupe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from Crawler import Crawler_MunicipalYaml as router

    captured: dict[str, Any] = {}

    def collect(*_args: Any, **kwargs: Any):
        captured.update(kwargs)
        return [], "gbe-experience", {"snapshot_complete": True}

    monkeypatch.setattr(gbe, "collect_gyeongbuk_edu_experience", collect)
    target = router.CrawlTarget(
        provider=gbe.GYEONGBUK_EDU_EXPERIENCE_PROVIDER,
        name="경상북도교육청 견학·체험",
        branch="경상북도교육청 통합예약",
        url=gbe.GYEONGBUK_EDU_EXPERIENCE_URL,
        source="test",
    )
    router.collect_from_url(
        target,
        timeout=1,
        max_depth=0,
        max_pages=20,
        detail_limit=100,
    )

    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])
    assert "allow_raw_requests_for_tests" not in captured


def test_target_operational_and_coverage_are_exactly_integrated() -> None:
    target_path = ROOT / "config/crawl_targets/municipal_integrated_reservation.yaml"
    targets = yaml.safe_load(target_path.read_text(encoding="utf-8"))["targets"]
    matches = [
        item
        for item in targets
        if item.get("provider") == gbe.GYEONGBUK_EDU_EXPERIENCE_PROVIDER
    ]
    assert len(matches) == 1
    target = matches[0]
    assert target["url"] == gbe.GYEONGBUK_EDU_EXPERIENCE_URL
    assert target["crawler_module"] == (
        "Crawler.municipal_gyeongbuk_education_experience"
    )
    assert target["crawler_callable"] == "collect_gyeongbuk_edu_experience"
    assert target["ops_scopes"] == ["experience"]
    assert target["full_snapshot_required"] is True
    assert target["last_quality"]["collected"] == 75
    assert {item["code"] for item in target["covered_municipalities"]} == {
        item["code"]
        for item in gbe.GYEONGBUK_EDU_EXPERIENCE_COVERED_MUNICIPALITIES
    }

    operational = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )["entries"]
    entries = [
        item
        for item in operational
        if item.get("provider") == gbe.GYEONGBUK_EDU_EXPERIENCE_PROVIDER
    ]
    assert len(entries) == 1
    assert entries[0]["target_url"] == gbe.GYEONGBUK_EDU_EXPERIENCE_URL
    assert entries[0]["row_count"] == 75

    coverage = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_coverage.yaml").read_text(
            encoding="utf-8"
        )
    )
    for expected in gbe.GYEONGBUK_EDU_EXPERIENCE_COVERED_MUNICIPALITIES:
        municipality = next(
            item
            for item in coverage["municipalities"]
            if item.get("code") == expected["code"]
        )
        assert gbe.GYEONGBUK_EDU_EXPERIENCE_PROVIDER in municipality["owner_providers"]
        assert gbe.GYEONGBUK_EDU_EXPERIENCE_PROVIDER in municipality["promoted_providers"]
        assert gbe.GYEONGBUK_EDU_EXPERIENCE_PROVIDER in municipality[
            "yaml_owner_providers"
        ]
        assert any(
            evidence.get("provider") == gbe.GYEONGBUK_EDU_EXPERIENCE_PROVIDER
            and evidence.get("target_url") == gbe.GYEONGBUK_EDU_EXPERIENCE_URL
            and evidence.get("row_count") == 75
            for evidence in municipality["evidence"]
        )


def test_live_baseline_and_exact_venue_registry_are_explicit() -> None:
    assert gbe.GYEONGBUK_EDU_EXPERIENCE_LIVE_BASELINE["source_total"] == 145
    assert gbe.GYEONGBUK_EDU_EXPERIENCE_LIVE_BASELINE["current_count"] == 82
    assert gbe.GYEONGBUK_EDU_EXPERIENCE_LIVE_BASELINE["returned_count"] == 75
    assert len(gbe.GYEONGBUK_EDU_EXPERIENCE_COVERED_MUNICIPALITIES) == 7
    assert {venue.municipality_code for venue in gbe.GYEONGBUK_EDU_EXPERIENCE_VENUES.values()} == {
        "4711000000",
        "4713000000",
        "4717000000",
        "4725000000",
        "4729000000",
        "4773000000",
        "4785000000",
    }


@pytest.mark.skipif(
    __import__("os").environ.get("RUN_LIVE_CRAWLER_TESTS") != "1",
    reason="set RUN_LIVE_CRAWLER_TESTS=1 for official live verification",
)
def test_live_official_snapshot() -> None:
    rows, _, meta = gbe.collect(
        _target(),
        today="2026-08-05",
        max_pages=20,
        detail_limit=100,
        allow_raw_requests_for_tests=True,
    )
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] >= 1
    assert meta["detail_pages"] == meta["source_current_count"]
    assert len(rows) == meta["returned_count"]
    assert meta["unsafe_endpoint_calls"] == 0

