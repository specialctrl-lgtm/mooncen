from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from bs4 import BeautifulSoup

import Crawler.Crawler_GeneratedYamlTargets as generated
import Crawler.Crawler_MunicipalYaml as municipal


ROOT = Path(__file__).resolve().parents[1]
PROVIDER = municipal.DOBONG_INTEGRATED_PROVIDER
LIST_URL = municipal.DOBONG_INTEGRATED_LIST_URL
FACILITY_PROVIDER = "MUNI_YEYAK_DOBONGSISEOL_OR_KR_0A2D506D"
LEGACY_PROVIDERS = {
    "MUNI_EDU_DOBONG_GO_KR_5ADA6E67",
    "MUNI_EDU_DOBONG_GO_KR_779522F2",
    "MUNI_EDU_DOBONG_GO_KR_905EFB5D",
}


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "html.parser")


def _target(url: str = LIST_URL, provider: str = PROVIDER) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=provider,
        name="도봉구 통합예약 교육·강좌",
        branch="도봉구 통합예약",
        url=url,
        source="test",
        priority=2,
        region="서울특별시 도봉구",
        extra={},
    )


def _list_row(
    list_id: int,
    record_id: str,
    title: str,
    *,
    agency: str,
    status: str,
    period: str,
    linked_url: str = "",
) -> str:
    if linked_url:
        onclick = (
            f"goLinkAjax('{linked_url}','{record_id}','20000','1','GnbTp1',"
            f"'GnbTp109','{title}');"
        )
    else:
        onclick = f"goDesc('{record_id}','');"
    return (
        "<tr>"
        f"<td>{list_id}</td><td>교양</td><td>{agency}</td>"
        f"<td><a href='#' onclick=\"{onclick}\">{title}</a></td>"
        "<td>테스트 교육장</td><td>26.07.01 ~ 26.07.31</td>"
        f"<td>{status}</td><td>온라인</td></tr>"
        f"<tr><td colspan='8'>{period}</td></tr>"
    )


def _native_detail(
    title: str,
    *,
    agency: str,
    location: str,
    status: str,
    period: str,
) -> BeautifulSoup:
    pairs = [
        ("기관명", agency),
        ("주소", "서울시 도봉구 마들로 656"),
        ("모집접수명", title),
        ("카테고리", "교양"),
        ("상태", status),
        ("접수기간", "26.07.01 09:00:00 ~ 26.07.31 18:00:00"),
        ("진행기간", period),
        ("접수방법", "온라인"),
        ("문의", "02-2091-0000"),
        ("교육대상", "도봉구민 / 성인"),
        ("지역/장소", location),
        ("수강인원", "인원제한 있음 정원 : (25 / 5명 가능) (온라인 : 25명)"),
        ("선정방식", "선착순"),
        ("수강료", "30,000원 (현장입금)"),
        ("강의시간", "화(10:00~12:00)"),
    ]
    table_rows = "".join(f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in pairs)
    return _soup(
        "<html><body>"
        f"<table><tbody>{table_rows}</tbody></table>"
        f"<div class='view_contents'>{title} 상세 안내 재료비 5,000원</div>"
        "</body></html>"
    )


def _external_detail() -> BeautifulSoup:
    description = (
        "○ 위    치 : 도봉구청 지하1층 교육장 "
        "○ 교육대상 : 교육을 희망하는 도봉구민 누구나 "
        "○ 교육내용 : 안전 교육과 실습 "
        "○ 교육시간 : 10:00~11:30 "
        "○ 인      원 : 40명/1회 "
        "○ 접수방법 : 전화, 방문 또는 인터넷 접수 "
        "○ 문      의 : 의약과 02-2091-4507 (본 교육은 전액 무료)"
    )
    return _soup(
        "<html><body><table><tbody>"
        "<tr><th>예약명</th><td>심폐소생술 교육</td></tr>"
        f"<tr><th>설명</th><td>{description}</td></tr>"
        "<tr><th>신청 기간</th><td>2026-01-01 ~ 2026-12-31</td></tr>"
        "</tbody></table></body></html>"
    )


@pytest.fixture
def dobong_fixture(monkeypatch: pytest.MonkeyPatch) -> tuple[list[tuple[str, int]], list[str]]:
    external_url = "https://www.dobong.go.kr/wdb_dev/receipt/ReceiptView.asp?RECEIPT_MST_NUM=906"
    source_rows = [
        _list_row(
            5,
            "336296790",
            "심폐소생술 오전반",
            agency="도봉구청",
            status="상시접수",
            period="26.08.01 ~ 26.08.01",
            linked_url=external_url,
        ),
        _list_row(
            4,
            "336296791",
            "심폐소생술 오후반",
            agency="도봉구청",
            status="접수중",
            period="상세참고",
            linked_url=external_url,
        ),
        _list_row(
            3,
            "3917",
            "스마트폰 활용",
            agency="교육포털",
            status="안내중",
            period="26.08.04 ~ 26.08.27",
        ),
        _list_row(
            2,
            "3810",
            "창1동 요가",
            agency="자치회관",
            status="접수마감",
            period="26.07.01 ~ 26.09.30",
        ),
        _list_row(
            1,
            "3700",
            "종료된 강좌",
            agency="자치회관",
            status="접수마감",
            period="26.06.01 ~ 26.07.18",
        ),
    ]
    pages = {
        1: _soup(f"<table><tbody>{''.join(source_rows[:2])}</tbody></table>"),
        2: _soup(f"<table><tbody>{''.join(source_rows[2:4])}</tbody></table>"),
        3: _soup(f"<table><tbody>{source_rows[4]}</tbody></table>"),
    }
    details = {
        "3917": _native_detail(
            "스마트폰 활용",
            agency="교육포털",
            location="도봉1동 / 도봉구청 지하1층 주민전산교육장",
            status="안내중",
            period="26.08.04 13:00:00 ~ 26.08.27 15:30:00",
        ),
        "3810": _native_detail(
            "창1동 요가",
            agency="자치회관",
            location="창1동 / 4층 강당",
            status="접수마감",
            period="26.07.01 09:00:00 ~ 26.09.30 18:00:00",
        ),
    }
    list_calls: list[tuple[str, int]] = []
    detail_calls: list[str] = []
    monkeypatch.setattr(municipal, "DOBONG_INTEGRATED_PAGE_SIZE", 2)
    monkeypatch.setattr(municipal, "dobong_integrated_today", lambda: date(2026, 7, 19))
    monkeypatch.setattr(municipal, "session", lambda: object())

    def fetch_soup(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.netloc == municipal.DOBONG_INTEGRATED_HOST and parsed.path == municipal.DOBONG_INTEGRATED_LIST_PATH:
            page = int(query["Page"][0])
            assert query["PageSize"] == ["2"]
            list_calls.append((parsed.path, page))
            return pages[page]
        detail_calls.append(url)
        if parsed.netloc == municipal.DOBONG_INTEGRATED_HOST:
            return details[query["Idx"][0]]
        assert url == external_url
        return _external_detail()

    monkeypatch.setattr(municipal, "fetch_soup", fetch_soup)
    return list_calls, detail_calls


def test_dobong_full_history_filters_current_enriches_details_and_splits_branches(
    dobong_fixture: tuple[list[tuple[str, int]], list[str]],
) -> None:
    list_calls, detail_calls = dobong_fixture

    rows, parser, meta = municipal.collect_from_url(
        _target(), timeout=5, max_depth=0, max_pages=10, detail_limit=10
    )

    assert parser == municipal.DOBONG_INTEGRATED_PARSER
    assert len(rows) == 4
    assert meta["declared_total"] == 5
    assert meta["declared_pages"] == meta["pages"] == 3
    assert meta["expired_count"] == 1
    assert meta["unknown_period_count"] == 1
    assert meta["detail_pages"] == meta["detail_candidates"] == 3
    assert meta["linked_external_count"] == 2
    assert meta["linked_external_unique_details"] == 1
    assert meta["native_count"] == 2
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_cap_reached"] is False
    assert "configured_collection_error" not in meta
    assert list_calls == [
        (municipal.DOBONG_INTEGRATED_LIST_PATH, 1),
        (municipal.DOBONG_INTEGRATED_LIST_PATH, 2),
        (municipal.DOBONG_INTEGRATED_LIST_PATH, 3),
    ]
    assert len(detail_calls) == 3
    assert len({row["provider_course_id"] for row in rows}) == 4
    assert len({row["raw_url"] for row in rows}) == 4
    assert Counter(row["status"] for row in rows) == {
        "OPEN": 2,
        "SCHEDULED": 1,
        "CLOSED": 1,
    }
    assert Counter(row["branch"] for row in rows) == {
        "도봉구청": 2,
        "도봉구 교육포털": 1,
        "창1동 자치회관": 1,
    }
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["source_group"] == "municipal_reservation" for row in rows)
    assert all(row["municipality_code"] == "1132000000" for row in rows)

    external = [row for row in rows if row["raw_fields"]["link_type"] == "linked_external"]
    assert all(row["target"] == "교육을 희망하는 도봉구민 누구나" for row in external)
    assert all(row["capacity_total"] == 40 for row in external)
    assert all(row["fee"] == "무료" for row in external)
    assert all(row["reservation_available"] is True for row in external)
    assert all(row["application_url"].startswith("https://www.dobong.go.kr/") for row in external)

    scheduled = next(row for row in rows if row["status"] == "SCHEDULED")
    assert scheduled["venue_name"] == "도봉구청 지하1층 주민전산교육장"
    assert scheduled["capacity_current"] == 20
    assert scheduled["capacity_total"] == 25
    assert scheduled["capacity_remaining"] == 5
    assert "application_url" not in scheduled


def test_dobong_caps_block_complete_snapshot(
    dobong_fixture: tuple[list[tuple[str, int]], list[str]],
) -> None:
    rows, _parser, meta = municipal.collect_dobong_integrated_education(
        _target(), timeout=5, max_pages=10, detail_limit=1
    )

    assert rows
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    "target",
    [
        _target(provider="MUNI_OTHER"),
        _target("http://yeyak.dobong.go.kr/recruit/Education.asp?Gnb=GnbTp1&MCode=UMA1001"),
        _target("https://yeyak.dobong.go.kr/recruit/Education.asp?Gnb=GnbTp2&MCode=UMA1001"),
        _target("https://yeyak.dobong.go.kr/recruit/Culture.asp?Gnb=GnbTp1&MCode=UMA1001"),
    ],
)
def test_dobong_route_is_exact(target: municipal.CrawlTarget) -> None:
    assert municipal.is_dobong_integrated_education_target(target) is False


def test_dobong_target_ownership_and_full_snapshot_arguments() -> None:
    public = yaml.safe_load(
        (ROOT / "config" / "crawl_targets" / "public_reservation.yaml").read_text(encoding="utf-8")
    )
    canonical = next(row for row in public["targets"] if row.get("provider") == PROVIDER)
    assert canonical["url"] == LIST_URL
    assert canonical["crawler_status"] == "ready"
    assert canonical["collection_type"] == "full_pagination+current_detail_html"
    assert canonical["collection_category"] == "공공예약"
    assert canonical["domain_category"] == "교육·강좌"
    assert canonical["source_group"] == "municipal_reservation"
    assert canonical["service_group"] == "공공강좌"
    assert canonical["service_group_policy"] == "locked"
    assert canonical["full_snapshot_required"] is True
    assert canonical["municipality_code"] == "1132000000"
    assert canonical["origin"] == "live_validated"

    lifelong = yaml.safe_load(
        (ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml").read_text(encoding="utf-8")
    )
    duplicates = [row for row in lifelong["targets"] if row.get("provider") in LEGACY_PROVIDERS]
    assert len(duplicates) == 3
    assert all(row["collection_type"] == "duplicate" for row in duplicates)
    assert all(row["crawler_status"] == f"duplicate_url:{PROVIDER}" for row in duplicates)
    assert all(row["duplicate_of"] == PROVIDER for row in duplicates)
    assert all(row["superseded_by"] == PROVIDER for row in duplicates)

    arguments = list(generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[PROVIDER])
    assert arguments == [
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "30",
        "--detail-limit",
        "200",
    ]

    operational = yaml.safe_load(
        (ROOT / "config" / "municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )
    providers = {row["provider"] for row in operational["entries"]}
    assert {PROVIDER, FACILITY_PROVIDER}.issubset(providers)
