from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml
from bs4 import BeautifulSoup

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import municipal_geumcheon as geumcheon


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Target:
    provider: str
    url: str


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target() -> Target:
    return Target(geumcheon.GEUMCHEON_PROVIDER, geumcheon.GEUMCHEON_URL)


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _list_row(
    lecture_key: str,
    *,
    current: bool,
    status: str = "모집마감 교육 중",
) -> str:
    period = "2099.07.01~2099.08.31" if current else "2020.01.01~2020.01.31"
    return f"""
      <tr>
        <td>{status}</td>
        <td class="p-subject">
          <a href="/reserve/edcLctreView.do?key=112&amp;searchLctreKey={lecture_key}">
            공식 금천 강좌 {lecture_key}
          </a>
          <p>금천 교육장 {lecture_key}</p>
        </td>
        <td>금천구민</td>
        <td>신청 : 2099.06.01~2099.06.30 교육 : {period} (화)</td>
        <td>1/20</td>
        <td>무료</td>
        <td>온라인</td>
      </tr>
    """


def _list_page(
    rows: str,
    total: int,
    *,
    page_index: int = 1,
    total_pages: int = 1,
) -> str:
    return f"""
      <html><body>
        <div class="p-total">총 {total:,} 건 [ {page_index}/{total_pages} 페이지 ]</div>
        <table><tbody>{rows}</tbody></table>
      </body></html>
    """


def _detail_page(
    lecture_key: str,
    *,
    applicable: bool,
    include_agree: bool = True,
    category: str = "문화예술",
) -> str:
    agree = (
        f'<a href="./webEdcLctreAgree.do?key=112&amp;lctreKey={lecture_key}">신청</a>'
        if applicable and include_agree
        else ""
    )
    return f"""
      <html><body>
        <table><tbody>
          <tr><th>강좌영역</th><td>{category}</td><th>신청기간</th><td>2099.06.01(월) 10:00 ~ 2099.06.30(화) 18:00</td></tr>
          <tr><th>교육기간</th><td>2099.07.01(수) ~ 2099.08.31(월)</td><th>강의시간</th><td>(화) 10:00~12:00</td></tr>
          <tr><th>수강신청방법</th><td>온라인</td><th>강의장소</th><td>금천교육장 08611 서울특별시 금천구 시흥대로73길 70</td></tr>
          <tr><th>수강대상</th><td>금천구민</td><th>정원</th><td>1/20</td></tr>
          <tr><th>수강료</th><td>무료</td><th>주최</th><td>금천구청</td></tr>
          <tr><th>문의</th><td>02-2627-0000</td><th>선별방법</th><td>선착순</td></tr>
        </tbody></table>
        {agree}
      </body></html>
    """


def test_official_course_area_routes_experience_without_reclassifying_education() -> None:
    for category, expected in (
        ("체험/견학", ("체험", "체험·견학", "체험")),
        ("문화예술", ("강좌", "교육·강좌", "공공강좌")),
    ):
        identity = "157130"
        row = geumcheon._base_row(
            _target(),
            identity,
            "공식 금천 프로그램",
            geumcheon.geumcheon_detail_url(identity),
        )
        row.update(
            {
                "period": "2099-07-01 ~ 2099-08-31",
                "apply_period": "2099-06-01 ~ 2099-06-30",
            }
        )
        row["raw_fields"]["source_status"] = "모집마감 교육 중"

        errors = geumcheon._detail(
            row,
            _soup(_detail_page(identity, applicable=False, category=category)),
        )

        assert errors == []
        assert row["category"] == category
        assert (
            row["program_type"],
            row["domain_category"],
            row["service_group"],
        ) == expected
        assert row["service_group_policy"] == "locked"
        assert row["raw_fields"]["official_course_area"] == category


def test_exact_provider_and_canonical_url_validator() -> None:
    assert geumcheon.is_geumcheon_target(_target()) is True
    assert geumcheon.is_geumcheon_target(
        Target("WRONG", geumcheon.GEUMCHEON_URL)
    ) is False
    assert geumcheon.is_geumcheon_target(
        Target(
            geumcheon.GEUMCHEON_PROVIDER,
            geumcheon.GEUMCHEON_URL + "&searchLctreGroup=42",
        )
    ) is False
    assert geumcheon.is_geumcheon_target(
        Target(
            geumcheon.GEUMCHEON_PROVIDER,
            "https://www.geumcheon.go.kr/reserve/webEdcLctreList.do?rep=1&key=112",
        )
    ) is False


def test_complete_973_snapshot_filters_230_current_and_preserves_21_groups() -> None:
    all_ids = [str(156000 + index) for index in range(973)]
    current_ids = set(all_ids[:230])
    group_codes = [code for code, _label in geumcheon.GEUMCHEON_GROUPS]
    ids_by_group: dict[str, list[str]] = {code: [] for code in group_codes}
    for index, identity in enumerate(all_ids):
        ids_by_group[group_codes[index % len(group_codes)]].append(identity)

    def source_status(identity: str) -> str:
        if identity == all_ids[0]:
            return "모집 중 교육대기"
        if identity == all_ids[1]:
            return "우선접수 교육대기"
        return "모집마감 교육 중"

    all_html = _list_page(
        "".join(
            _list_row(
                identity,
                current=identity in current_ids,
                status=source_status(identity),
            )
            for identity in all_ids
        ),
        973,
    )
    group_html = {
        code: _list_page(
            "".join(
                _list_row(
                    identity,
                    current=identity in current_ids,
                    status=source_status(identity),
                )
                for identity in identities
            ),
            len(identities),
        )
        for code, identities in ids_by_group.items()
    }
    fetched_details: list[str] = []
    fetched_lists: list[str] = []
    lock = Lock()

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 9
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == geumcheon.GEUMCHEON_LIST_PATH:
            assert query["pageUnit"] == ["1000"]
            assert query["pageIndex"] == ["1"]
            group = (query.get("searchLctreGroup") or [""])[0]
            with lock:
                fetched_lists.append(group)
            return _soup(group_html[group] if group else all_html)
        assert parsed.path == geumcheon.GEUMCHEON_DETAIL_PATH
        identity = query["searchLctreKey"][0]
        assert identity in current_ids
        with lock:
            fetched_details.append(identity)
        return _soup(
            _detail_page(identity, applicable=identity in {all_ids[0], all_ids[1]})
        )

    dedupe_calls: list[int] = []

    def dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        dedupe_calls.append(len(rows))
        return rows

    result, parser, meta = geumcheon.collect_geumcheon_current(
        _target(),
        timeout=9,
        detail_limit=230,
        fetcher=fetch,
        session_factory=DummySession,
        dedupe_rows=dedupe,
        today="2026-07-19",
        max_workers=4,
    )

    assert parser == geumcheon.GEUMCHEON_PARSER
    assert len(result) == 230
    assert meta["total_count"] == 973
    assert meta["discovered_links"] == 973
    assert meta["expired_count"] == 743
    assert meta["current_count"] == 230
    assert meta["returned_count"] == 230
    assert meta["list_requests"] == 22
    assert meta["group_count"] == 21
    assert meta["detail_required_count"] == 230
    assert meta["detail_attempts"] == 230
    assert meta["detail_pages"] == 230
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert "configured_collection_error" not in meta
    assert sorted(fetched_details) == sorted(current_ids)
    assert len(fetched_lists) == 22
    assert dedupe_calls == [230]

    expected_branches = {label for _code, label in geumcheon.GEUMCHEON_GROUPS}
    assert {row["branch"] for row in result} == expected_branches
    assert all(row["preserve_branch"] is True for row in result)
    assert all(row["branch_code"].startswith("GEUMCHEON_BRANCH_") for row in result)
    assert all(row["municipality_code"] == "1154500000" for row in result)
    for row in result:
        identity = row["raw_fields"]["lecture_key"]
        assert row["provider_course_id"] == (
            f"{geumcheon.GEUMCHEON_PROVIDER}:edc:{identity}"
        )
        if identity in {all_ids[0], all_ids[1]}:
            assert row["application_url"] == geumcheon.geumcheon_agree_url(identity)
            assert row["reservation_available"] is True
        else:
            assert "application_url" not in row
            assert row["reservation_available"] is False


def test_server_page_unit_cap_collects_all_1024_rows_across_two_pages() -> None:
    all_ids = [str(200000 + index) for index in range(1024)]
    current_ids = set(all_ids[:2])
    group_codes = [code for code, _label in geumcheon.GEUMCHEON_GROUPS]
    ids_by_group: dict[str, list[str]] = {code: [] for code in group_codes}
    for index, identity in enumerate(all_ids):
        ids_by_group[group_codes[index % len(group_codes)]].append(identity)

    all_pages = {
        1: _list_page(
            "".join(
                _list_row(identity, current=identity in current_ids)
                for identity in all_ids[:1000]
            ),
            1024,
            page_index=1,
            total_pages=2,
        ),
        2: _list_page(
            "".join(
                _list_row(identity, current=identity in current_ids)
                for identity in all_ids[1000:]
            ),
            1024,
            page_index=2,
            total_pages=2,
        ),
    }
    group_html = {
        code: _list_page(
            "".join(
                _list_row(identity, current=identity in current_ids)
                for identity in identities
            ),
            len(identities),
        )
        for code, identities in ids_by_group.items()
    }
    fetched_all_pages: list[int] = []
    lock = Lock()

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == geumcheon.GEUMCHEON_LIST_PATH:
            page_index = int(query["pageIndex"][0])
            group = (query.get("searchLctreGroup") or [""])[0]
            if group:
                assert page_index == 1
                return _soup(group_html[group])
            with lock:
                fetched_all_pages.append(page_index)
            return _soup(all_pages[page_index])
        identity = query["searchLctreKey"][0]
        assert identity in current_ids
        return _soup(_detail_page(identity, applicable=False))

    result, parser, meta = geumcheon.collect_geumcheon_current(
        _target(),
        max_pages=30,
        detail_limit=2,
        fetcher=fetch,
        session_factory=DummySession,
        today="2026-07-19",
        max_workers=4,
    )

    assert parser == geumcheon.GEUMCHEON_PARSER
    assert len(result) == 2
    assert meta["total_count"] == 1024
    assert meta["discovered_links"] == 1024
    assert meta["total_pages"] == 2
    assert meta["list_requests"] == 23
    assert meta["required_list_requests"] == 23
    assert meta["pagination_detected"] is True
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
    assert sorted(fetched_all_pages) == [1, 2]


def test_dynamic_second_page_requirement_respects_max_pages_before_fanout() -> None:
    first_page = _list_page(
        _list_row("209999", current=False),
        1024,
        page_index=1,
        total_pages=2,
    )
    fetch_calls: list[str] = []

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        fetch_calls.append(url)
        assert len(fetch_calls) == 1
        return _soup(first_page)

    result, _parser, meta = geumcheon.collect_geumcheon_current(
        _target(),
        max_pages=22,
        detail_limit=0,
        fetcher=fetch,
        session_factory=DummySession,
        today="2026-07-19",
    )

    assert result == []
    assert len(fetch_calls) == 1
    assert meta["required_list_requests"] == 23
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert "22 of 23 required list requests" in meta["configured_collection_error"]


def test_declared_count_mismatch_returns_no_partial_rows() -> None:
    html = _list_page(_list_row("157001", current=True), 2)

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        return _soup(html)

    result, _parser, meta = geumcheon.collect_geumcheon_current(
        _target(),
        fetcher=fetch,
        session_factory=DummySession,
        today="2026-07-19",
    )

    assert result == []
    assert meta["snapshot_complete"] is False
    assert meta["pagination_complete"] is False
    assert meta["detail_attempts"] == 0
    assert "declared 2" in meta["configured_collection_error"]


def test_applicable_course_without_official_agree_link_fails_closed() -> None:
    identity = "157001"
    all_html = _list_page(
        _list_row(identity, current=True, status="모집 중 교육대기"), 1
    )
    first_group = geumcheon.GEUMCHEON_GROUPS[0][0]

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == geumcheon.GEUMCHEON_LIST_PATH:
            group = (query.get("searchLctreGroup") or [""])[0]
            if not group:
                return _soup(all_html)
            return _soup(
                all_html if group == first_group else _list_page("", 0)
            )
        return _soup(
            _detail_page(identity, applicable=True, include_agree=False)
        )

    result, _parser, meta = geumcheon.collect_geumcheon_current(
        _target(),
        fetcher=fetch,
        session_factory=DummySession,
        today="2026-07-19",
    )

    assert result == []
    assert meta["current_count"] == 1
    assert meta["returned_count"] == 0
    assert meta["detail_pages"] == 1
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert "has no canonical agree link" in meta["configured_collection_error"]


def test_raw_requests_are_not_an_implicit_production_fallback() -> None:
    result, _parser, meta = geumcheon.collect_geumcheon_current(
        _target(), today="2026-07-19"
    )

    assert result == []
    assert meta["snapshot_complete"] is False
    assert "injection are required" in meta["configured_collection_error"]


def test_max_pages_below_22_fails_before_any_network_request() -> None:
    fetch_calls: list[str] = []
    session_calls: list[bool] = []

    def session_factory() -> DummySession:
        session_calls.append(True)
        return DummySession()

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        fetch_calls.append(url)
        raise AssertionError("network must not be touched when the list cap is short")

    result, _parser, meta = geumcheon.collect_geumcheon_current(
        _target(),
        max_pages=21,
        fetcher=fetch,
        session_factory=session_factory,
        today="2026-07-19",
    )

    assert result == []
    assert fetch_calls == []
    assert session_calls == []
    assert meta["max_pages"] == 21
    assert meta["required_list_requests"] == 22
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert "21 of 22 required list requests" in meta["configured_collection_error"]


def test_geumcheon_target_requires_a_complete_scheduled_snapshot() -> None:
    document = yaml.safe_load(
        (
            ROOT / "config" / "crawl_targets" / "public_reservation.yaml"
        ).read_text(encoding="utf-8")
    )
    target = next(
        row
        for row in document["targets"]
        if row.get("provider") == geumcheon.GEUMCHEON_PROVIDER
    )
    assert target["url"] == geumcheon.GEUMCHEON_URL
    assert target["crawler_status"] == "ready"
    assert target["collection_type"] == geumcheon.GEUMCHEON_PARSER
    assert target["source_group"] == "municipal_reservation"
    assert target["domain_category"] == "교육·강좌"
    assert target["service_group"] == "공공강좌"
    assert target["service_group_policy"] == "locked"
    assert target["full_snapshot_required"] is True
    assert target["municipality_code"] == "1154500000"
    assert target["last_quality"]["collected"] == 230
    assert target["last_quality"]["source_total"] == 973
    assert target["last_quality"]["snapshot_complete"] is True

    arguments = list(
        generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[geumcheon.GEUMCHEON_PROVIDER]
    )
    assert arguments == [
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "30",
        "--detail-limit",
        "300",
    ]
