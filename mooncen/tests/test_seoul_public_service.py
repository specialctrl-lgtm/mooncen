from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as municipal_runner
from Crawler import Crawler_GeneratedYamlTargets as generated_runner
from Crawler import Crawler_SeoulPublicService as seoul_wrapper
from Crawler import municipal_seoul_public_service as seoul


SERVICE_ID = "S260719155749290543"


@dataclass
class _Response:
    text: str
    url: str
    status_code: int = 200

    def __post_init__(self) -> None:
        self.content = self.text.encode("utf-8")
        self.headers = {"Content-Type": "text/html;charset=utf-8"}
        self.history: list[Any] = []


def _district_controls() -> str:
    return "".join(
        f'<input name="sch_pl" value="{district.source_code}" '
        f"onclick=\"fnChoose(this,'{district.label}','{district.source_code}');\" />"
        for district in seoul.SEOUL_DISTRICTS
    )


def _card(
    *,
    title: str,
    status_label: str = "접수중",
    category_label: str = "교육강좌",
    method_label: str = "온라인",
    external: bool = False,
) -> str:
    onclick = (
        "fnNewPop('https://example.org/public-info'); return false;"
        if external
        else f"fnDetailPage('{SERVICE_ID}', '', ''); return false;"
    )
    return f"""
    <li>
      <a href="#" onclick="{onclick}">
        <div class="img_box"><span class="bd_label">{status_label}</span></div>
        <div class="con_box">
          <ul class="ib_type"><li>{category_label}</li><li>기타</li></ul>
          <h4 class="tit1">{title}</h4>
          <ul class="ib_attr">
            <li><b class="place">장소명</b>서울시청 교육장(강남구)</li>
            <li><b class="date1">접수기간</b>2026.07.29 ~ 2026.08.05</li>
            <li><b class="date2">이용기간</b>2026.08.12 ~ 2026.08.12</li>
          </ul>
          <span class="bd_ico online">{method_label}</span>
        </div>
      </a>
    </li>
    """


def _list_html(*, code: str, page: int, total: int, rows: str = "") -> str:
    return f"""
    <html><head><title>서울특별시 공공서비스예약</title></head><body>
      <form>
        <input name="code" value="{code}" />
        <input id="currentPage" name="currentPage" value="{page}" />
        {_district_controls()}
      </form>
      <div class="title_dep1">총 <span class="text_red">{total}</span> 건</div>
      <ul class="img_board">{rows}</ul>
    </body></html>
    """


def _detail_html(
    title: str,
    *,
    code: str = "T000",
    status_label: str = "접수중",
    method_label: str = "인터넷",
    place_html: str = "서울시청 교육장",
) -> str:
    application_control = (
        """
      <form id="aform"></form>
      <script>
        function fnRevervInsertForm() {
          $('#aform').attr('action', '/web/reservation/insertFormReserve.do');
        }
      </script>
      <a href="javascript:fnRevervInsertForm();">예약하기</a>
        """
        if "인터넷" in method_label and status_label != "온라인 예약불가"
        else ""
    )
    return f"""
    <html><head><title>서울특별시 공공서비스예약</title></head><body>
      <input type="hidden" name="code" value="{code}" />
      <input type="hidden" name="rsv_svc_id" value="{SERVICE_ID}" />
      <div class="dt_top_box">
        <span class="bd_label">{status_label}</span>
        <h4 class="dt_tit1"><span class="tit">{title}</span></h4>
      </div>
      <ul class="dt_top_list">
        <li><b class="tit1">장소</b>{place_html}</li>
        <li><b class="tit1">이용기간</b>2026.08.12 ~ 2026.08.12</li>
        <li><b class="tit1">접수기간</b>2026.07.29 09:00 ~ 2026.08.05 18:00</li>
        <li><b class="tit1">예약방법</b>{method_label}</li>
        <li><b class="tit1">문의전화</b>02-0000-0000</li>
      </ul>
      {application_control}
    </body></html>
    """


class _FakeSession:
    def __init__(
        self,
        *,
        title: str = "강남 시민 교육",
        category_code: str = "T000",
        category_label: str = "교육강좌",
        status_label: str = "접수중",
        method_label: str = "인터넷",
        place_html: str = "서울시청 교육장",
        external: bool = False,
        break_sentinel: bool = False,
    ) -> None:
        self.headers: dict[str, str] = {}
        self.title = title
        self.category_code = category_code
        self.category_label = category_label
        self.status_label = status_label
        self.method_label = method_label
        self.place_html = place_html
        self.external = external
        self.break_sentinel = break_sentinel
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.closed = False

    def post(self, url: str, *, data: dict[str, str], **_: Any) -> _Response:
        assert url == seoul.SEOUL_PUBLIC_SERVICE_LIST_ENDPOINT
        assert set(data) <= {"code", "sch_svc_sttus", "sch_pl", "currentPage"}
        assert data["code"] == self.category_code
        assert data["sch_svc_sttus"] in {"R403", "R402"}
        assert data.get("sch_pl", "") in {
            "",
            *(district.source_code for district in seoul.SEOUL_DISTRICTS),
        }
        page = int(data["currentPage"])
        district = data.get("sch_pl", "")
        owns_row = data["sch_svc_sttus"] == "R403" and district in {"", "SE01"}
        rows = ""
        total = int(owns_row)
        if owns_row and page == 1:
            rows = _card(
                title=self.title,
                status_label=self.status_label,
                category_label=self.category_label,
                method_label=self.method_label,
                external=self.external,
            )
        if self.break_sentinel and data["sch_svc_sttus"] == "R402" and not district and page == 2:
            rows = _card(
                title="sentinel leak",
                status_label="안내중",
                category_label=self.category_label,
                method_label=self.method_label,
            )
        self.calls.append(("POST", url, dict(data)))
        return _Response(
            _list_html(code=self.category_code, page=page, total=total, rows=rows),
            url,
        )

    def get(self, url: str, **_: Any) -> _Response:
        assert url == (seoul.SEOUL_PUBLIC_SERVICE_DETAIL_ENDPOINT + f"?rsv_svc_id={SERVICE_ID}")
        assert "insertFormReserve.do" not in url
        assert "login" not in url.lower()
        self.calls.append(("GET", url, {}))
        return _Response(
            _detail_html(
                self.title,
                code=self.category_code,
                status_label=self.status_label,
                method_label=self.method_label,
                place_html=self.place_html,
            ),
            url,
        )

    def close(self) -> None:
        self.closed = True


def _target(url: str = seoul.SEOUL_EDUCATION_URL) -> dict[str, str]:
    return {
        "provider": seoul.SEOUL_PUBLIC_SERVICE_PROVIDER,
        "url": url,
    }


def test_complete_fixture_census_maps_one_row_and_retains_zero_districts() -> None:
    fake = _FakeSession()
    rows, parser, meta = seoul.collect_seoul_public_service_courses(
        _target(),
        max_pages=200,
        detail_limit=5,
        max_workers=1,
        session_factory=lambda: fake,
    )

    assert parser == seoul.SEOUL_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["list_requests"] == 156
    assert meta["sentinel_requests"] == 52
    assert meta["stability_rechecks"] == 52
    assert meta["detail_pages"] == 1
    assert meta["application_endpoints_called"] == 0
    assert meta["pii_fields_stored"] == 0
    assert meta["global_totals"] == {"R403": 1, "R402": 0}
    assert len(meta["district_totals"]) == 25
    assert meta["district_totals"]["1168000000"]["total"] == 1
    assert meta["district_totals"]["1174000000"]["total"] == 0
    assert meta["district_returned_counts"]["1168000000"] == 1
    assert meta["district_returned_counts"]["1174000000"] == 0
    assert set(meta["district_provider_counts"].values()) == {1}

    assert len(rows) == 1
    row = rows[0]
    assert row["municipality_code"] == "1168000000"
    assert row["municipality_full_name"] == "서울특별시 강남구"
    assert row["municipality_region_verified"] is True
    assert row["domain_category"] == "교육·강좌"
    assert row["service_group"] == "공공강좌"
    assert row["classification_locked"] is True
    assert row["application_url"] == row["raw_url"]
    assert "insertFormReserve.do" not in row["application_url"]
    serialized = repr(row)
    assert "02-0000-0000" not in serialized
    assert Counter(method for method, _, _ in fake.calls) == {"POST": 156, "GET": 1}


def test_primary_period_ignores_a_nested_priority_booking_period() -> None:
    value = "2026.07.29 00:01 ~ 2026.11.05 14:01 (서울시민우선예약: 2026.07.29 00:01 ~ 2026.09.01 00:00)"
    assert seoul._date_range(value, "application") == (
        "2026-07-29",
        "2026-11-05",
    )


def test_experience_offline_row_keeps_locked_category_and_cleans_venue() -> None:
    fake = _FakeSession(
        title="강남 문화 체험",
        category_code="T200",
        category_label="문화체험",
        status_label="온라인 예약불가",
        method_label="전화",
        place_html=('문화실험공간 호수 (면적 : 624.17) <button class="btn_go_map">지도보기</button>'),
    )
    rows, _, meta = seoul.collect_seoul_public_service_courses(
        _target(seoul.SEOUL_EXPERIENCE_URL),
        max_pages=200,
        detail_limit=5,
        max_workers=1,
        session_factory=lambda: fake,
    )

    assert meta["snapshot_complete"] is True
    assert meta["global_totals"] == {"R403": 1, "R402": 0}
    assert len(rows) == 1
    row = rows[0]
    assert row["domain_category"] == "체험·견학"
    assert row["service_group"] == "체험"
    assert row["program_type"] == "체험"
    assert row["source_url"] == seoul.SEOUL_EXPERIENCE_URL
    assert row["branch"] == "문화실험공간 호수"
    assert row["venue_name"] == "문화실험공간 호수"
    assert row["status"] == "OPEN"
    assert row["reservation_available"] is False
    assert row["application_url"] == ""
    assert row["application_type"] == "INFO_ONLY"
    assert row["raw_fields"]["official_status_label"] == "온라인 예약불가"
    assert row["raw_fields"]["official_subcategory_labels"] == ["기타"]


def test_unknown_badge_in_audited_status_filter_fails_closed() -> None:
    fake = _FakeSession(status_label="알 수 없는 상태")
    rows, _, meta = seoul.collect_seoul_public_service_courses(
        _target(),
        max_pages=200,
        detail_limit=5,
        max_workers=1,
        session_factory=lambda: fake,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "status/filter mismatch" in meta["configured_collection_error"]
    assert not any(method == "GET" for method, _, _ in fake.calls)


@pytest.mark.parametrize(("total", "page"), [(0, 1), (1, 2)])
def test_official_empty_result_shape_may_omit_the_board(
    total: int,
    page: int,
) -> None:
    html = _list_html(code="T000", page=page, total=total).replace(
        '<ul class="img_board"></ul>',
        "",
    )
    if total == 0:
        html = html.replace(
            f'<input id="currentPage" name="currentPage" value="{page}" />',
            "",
        )
    parsed_total, rows = seoul._parse_page(
        BeautifulSoup(html, "lxml"),
        category=seoul.SEOUL_CATEGORIES["T000"],
        status=seoul.SEOUL_STATUSES[1],
        district=None,
        page=page,
        check_controls=page == 1,
    )

    assert parsed_total == total
    assert rows == []


def test_partition_retries_a_bounded_source_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    expected = seoul.PartitionSnapshot(
        status=seoul.SEOUL_STATUSES[0],
        district=None,
        total=0,
        pages=1,
        rows=(),
        page_counts={1: 0},
    )

    def fake_once(*_args: Any, **_kwargs: Any) -> seoul.PartitionSnapshot:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise seoul.SeoulPublicServiceContractError("partition total changed across pages")
        return expected

    monkeypatch.setattr(seoul, "_collect_partition_once", fake_once)
    monkeypatch.setattr(seoul.time, "sleep", lambda _seconds: None)

    result = seoul._collect_partition(
        object(),
        seoul.SEOUL_CATEGORIES["T200"],
        seoul.SEOUL_STATUSES[0],
        None,
    )
    assert result is expected
    assert attempts == 2


def test_partition_does_not_retry_an_unknown_contract_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def fake_once(*_args: Any, **_kwargs: Any) -> seoul.PartitionSnapshot:
        nonlocal attempts
        attempts += 1
        raise seoul.SeoulPublicServiceContractError("unknown source shape")

    monkeypatch.setattr(seoul, "_collect_partition_once", fake_once)
    with pytest.raises(
        seoul.SeoulPublicServiceContractError,
        match=r"T200/R403/ALL: unknown source shape",
    ):
        seoul._collect_partition(
            object(),
            seoul.SEOUL_CATEGORIES["T200"],
            seoul.SEOUL_STATUSES[0],
            None,
        )
    assert attempts == 1


def test_complete_census_retries_only_cross_partition_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    expected_rows = [{"provider_course_id": "stable"}]

    def fake_once(*_args: Any, **_kwargs: Any) -> tuple[list[Any], str, dict[str, Any]]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return (
                [],
                seoul.SEOUL_PARSER,
                {
                    "configured_collection_error": (
                        "SeoulPublicServiceContractError: district identity is absent from its global partition"
                    )
                },
            )
        return (
            expected_rows,
            seoul.SEOUL_PARSER,
            {
                "configured_collection_error": "",
                "snapshot_complete": True,
            },
        )

    monkeypatch.setattr(
        seoul,
        "_collect_seoul_public_service_courses_once",
        fake_once,
    )
    monkeypatch.setattr(seoul.time, "sleep", lambda _seconds: None)

    rows, parser, meta = seoul.collect_seoul_public_service_courses(_target())
    assert rows == expected_rows
    assert parser == seoul.SEOUL_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["snapshot_attempts"] == 2
    assert attempts == 2


@pytest.mark.parametrize(
    ("title", "external", "meta_key"),
    [
        ("[공지] 시스템 점검", False, "notice_count"),
        ("외부 기관 교육", True, "external_no_internal_detail_count"),
    ],
)
def test_notice_and_external_only_cards_are_counted_but_never_fetched(
    title: str, external: bool, meta_key: str
) -> None:
    fake = _FakeSession(title=title, external=external)
    rows, _, meta = seoul.collect_seoul_public_service_courses(
        _target(),
        max_pages=200,
        detail_limit=5,
        max_workers=1,
        session_factory=lambda: fake,
    )

    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta[meta_key] == 1
    assert not any(method == "GET" for method, _, _ in fake.calls)


def test_nonempty_immediate_sentinel_fails_closed() -> None:
    fake = _FakeSession(break_sentinel=True)
    rows, _, meta = seoul.collect_seoul_public_service_courses(
        _target(),
        max_pages=200,
        detail_limit=5,
        max_workers=1,
        session_factory=lambda: fake,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sentinel" in meta["configured_collection_error"]
    assert not any(method == "GET" for method, _, _ in fake.calls)


def test_request_cap_and_noncanonical_targets_fail_closed() -> None:
    fake = _FakeSession()
    rows, _, meta = seoul.collect_seoul_public_service_courses(
        _target(),
        max_pages=155,
        detail_limit=5,
        max_workers=1,
        session_factory=lambda: fake,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True

    for url in (
        seoul.SEOUL_PUBLIC_SERVICE_LIST_ENDPOINT,
        seoul.SEOUL_EDUCATION_URL + "&currentPage=1",
        seoul.SEOUL_EDUCATION_URL.replace("https://", "http://"),
    ):
        assert not seoul.is_seoul_public_service_target({"provider": seoul.SEOUL_PUBLIC_SERVICE_PROVIDER, "url": url})


def test_two_ready_config_siblings_cover_all_25_districts() -> None:
    config = yaml.safe_load(
        (Path(__file__).parents[1] / "config" / "crawl_targets" / "public_reservation.yaml").read_text(encoding="utf-8")
    )
    targets = [target for target in config["targets"] if target["provider"] == seoul.SEOUL_PUBLIC_SERVICE_PROVIDER]
    assert len(targets) == 2
    assert {target["crawler_status"] for target in targets} == {"ready"}
    assert {target["url"] for target in targets} == {
        seoul.SEOUL_EDUCATION_URL,
        seoul.SEOUL_EXPERIENCE_URL,
    }
    assert {target["domain_category"] for target in targets} == {
        "교육·강좌",
        "체험·견학",
    }
    expected_codes = {district.municipality_code for district in seoul.SEOUL_DISTRICTS}
    for target in targets:
        assert target["full_snapshot_required"] is True
        assert target["service_group_policy"] == "locked"
        assert {row["code"] for row in target["covered_municipalities"]} == expected_codes


@pytest.mark.parametrize(
    "url",
    [seoul.SEOUL_EDUCATION_URL, seoul.SEOUL_EXPERIENCE_URL],
)
def test_municipal_dispatch_uses_the_direct_collector(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    captured: dict[str, Any] = {}

    def fake_collect(target: Any, **kwargs: Any) -> tuple[list[Any], str, dict[str, Any]]:
        captured["target"] = target
        captured.update(kwargs)
        return [], "direct-seoul", {"snapshot_complete": True}

    monkeypatch.setattr(seoul, "collect_seoul_public_service_courses", fake_collect)
    target = municipal_runner.CrawlTarget(
        provider=seoul.SEOUL_PUBLIC_SERVICE_PROVIDER,
        name="서울 교육",
        branch="서울특별시",
        url=url,
        source="fixture",
    )
    result = municipal_runner.collect_from_url(
        target,
        timeout=17,
        max_pages=1_000,
        detail_limit=3_000,
    )

    assert result == ([], "direct-seoul", {"snapshot_complete": True})
    assert captured["target"] is target
    assert captured["timeout"] == 17
    assert captured["max_pages"] == 1_000
    assert captured["detail_limit"] == 3_000
    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])


def test_fixed_provider_wrapper_rejects_selection_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_main(
        argv: list[str],
        *,
        dedicated_provider: str = "",
    ) -> int:
        captured["argv"] = argv
        captured["dedicated_provider"] = dedicated_provider
        return 7

    monkeypatch.setattr(seoul_wrapper, "main", fake_main)
    assert seoul_wrapper.run(["--dry-run", "--max-pages", "1000"]) == 7
    assert captured == {
        "argv": [
            "--provider",
            seoul.SEOUL_PUBLIC_SERVICE_PROVIDER,
            "--dry-run",
            "--max-pages",
            "1000",
        ],
        "dedicated_provider": seoul.SEOUL_PUBLIC_SERVICE_PROVIDER,
    }
    for override in ("--all", "--write-registry", "--provider=HOMEPLUS"):
        with pytest.raises(SystemExit):
            seoul_wrapper.run([override])


def test_fixed_provider_wrapper_loads_both_official_ledgers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run_targets(targets: list[Any], **kwargs: Any) -> list[Any]:
        captured["targets"] = targets
        captured["kwargs"] = kwargs
        return [type("Report", (), {"success": True})()]

    monkeypatch.setattr(generated_runner, "run_targets", fake_run_targets)
    monkeypatch.setattr(generated_runner, "print_table", lambda _reports: None)
    monkeypatch.setattr(
        generated_runner,
        "write_report",
        lambda _reports: tmp_path / "seoul-report.json",
    )

    assert (
        seoul_wrapper.run(
            [
                "--dry-run",
                "--per-target-limit",
                "0",
                "--max-pages",
                "1000",
                "--detail-limit",
                "3000",
            ]
        )
        == 0
    )
    assert {target.url for target in captured["targets"]} == {
        seoul.SEOUL_EDUCATION_URL,
        seoul.SEOUL_EXPERIENCE_URL,
    }
    assert {target.provider for target in captured["targets"]} == {seoul.SEOUL_PUBLIC_SERVICE_PROVIDER}
    assert not any(target.extra.get("discover_from_main_url") for target in captured["targets"])
    assert captured["kwargs"]["complete_providers"] == {seoul.SEOUL_PUBLIC_SERVICE_PROVIDER}


def test_generic_yaml_loader_still_excludes_dedicated_seoul_provider() -> None:
    generic_targets = generated_runner.load_yaml_targets()
    assert not any(target["provider"] == seoul.SEOUL_PUBLIC_SERVICE_PROVIDER for target in generic_targets)
