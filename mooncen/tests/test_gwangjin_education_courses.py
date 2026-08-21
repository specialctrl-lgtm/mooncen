from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from bs4 import BeautifulSoup

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import Crawler_MunicipalYaml as municipal


ROOT = Path(__file__).resolve().parents[1]
EDU_PROVIDER = municipal.GWANGJIN_EDU_PROVIDER
FMCS_PROVIDER = municipal.GWANGJIN_FMCS_PROVIDER


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _edu_target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=EDU_PROVIDER,
        name="광진교육포털 전체프로그램",
        branch="광진교육포털",
        url=municipal.GWANGJIN_EDU_LIST_URL,
        source="test",
        priority=1,
        region="서울특별시 광진구",
    )


def _fmcs_target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=FMCS_PROVIDER,
        name="광진구통합예약시스템 수강신청",
        branch="광진구통합예약시스템",
        url=municipal.GWANGJIN_FMCS_LIST_URL,
        source="test",
        priority=1,
        region="서울특별시 광진구",
    )


def _edu_list_row(
    program_id: int,
    status: str,
    *,
    end_date: str,
    category_code: str,
    detail_code: str = "",
) -> str:
    return f"""
    <tr>
      <td><p class="bage">{status}</p></td>
      <td><a href="./view.do?progrmSn={program_id}&amp;ctgryCd={category_code}&amp;ctgryDetailCd={detail_code}&amp;menuNo=400008">
        {program_id}. 광진 교육 {program_id}
      </a></td>
      <td>월,수 10:00 ~ 12:00</td>
      <td>2099-08-01 ~ {end_date}</td>
      <td>2099-07-01 ~ 2099-07-31</td>
      <td>20명(5명) / 4명(1명) 【선착순】</td>
    </tr>
    """


def _edu_list_page(rows: str, total: int, page: int = 1, pages: int = 1) -> str:
    return f"""
    <html><body>
      <div>Total : {total} 건 [ {page} / {pages} pages ]</div>
      <table><tbody>{rows}</tbody></table>
    </body></html>
    """


def _edu_detail(program_id: str, *, application: bool) -> str:
    apply_link = ""
    if application:
        apply_link = (
            f'<a class="b-submit" href="/edu/pgm/edu/applcnt.do?progrmSeCd=02&amp;progrmSn={program_id}'
            '&amp;ctgryCd=0209&amp;menuNo=400008">신청하기</a>'
        )
    return f"""
    <html><body>
      <div class="viewt">
        <h2 class="subject">광진 교육 {program_id}<p class="bage">접수중</p></h2>
        <dl>
          <dt>접수기간</dt><dd>2099-07-01 09:00 ~ 2099-07-31 18:00</dd>
          <dt>접수현황</dt><dd>정원 20명(예비 5명) / 접수(대기자) : 4명 (1) 【선착순】</dd>
          <dt>교육기간</dt><dd>2099-08-01 ~ 2099-08-31</dd>
          <dt>교육시간</dt><dd>월,수 10:00 ~ 12:00</dd>
          <dt>교육대상</dt><dd>광진구민</dd>
          <dt>수강료</dt><dd>10,000원 (교재 재료비 별도 : 2,000원)</dd>
          <dt>교육장/강사</dt><dd>광진 강의실 {program_id} / 강사 {program_id} (강사)</dd>
          <dt>문의사항</dt><dd>02-450-0000</dd>
        </dl>
      </div>
      <div id="tab-area-1">공식 교육 안내 {program_id}</div>
      <div id="tab-area-3">공식 강의계획 {program_id}</div>
      <div class="btnSet">{apply_link}<a class="b-cancel">목록</a></div>
    </body></html>
    """


@pytest.fixture
def edu_site(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    current_open = _edu_list_row(9001, "접수중", end_date="2099-08-31", category_code="0209")
    current_scheduled = _edu_list_row(9002, "접수예정", end_date="2099-08-31", category_code="0214")
    expired = _edu_list_row(8001, "접수마감", end_date="2020-08-31", category_code="0211")
    page = _edu_list_page(current_open + current_scheduled + expired, total=3)
    fetched: list[str] = []

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == municipal.GWANGJIN_EDU_LIST_PATH:
            assert query["pageUnit"] == ["100"]
            assert query["pageIndex"] == ["1"]
            return _soup(page)
        assert parsed.path.endswith("/view.do")
        program_id = query["progrmSn"][0]
        return _soup(_edu_detail(program_id, application=program_id == "9001"))

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fetch)
    return fetched


def test_gwangjin_education_current_full_snapshot_and_actual_application(edu_site: list[str]) -> None:
    rows, parser, meta = municipal.collect_from_url(
        _edu_target(), timeout=5, max_depth=0, max_pages=5, detail_limit=10
    )

    assert parser == municipal.GWANGJIN_EDU_PARSER
    assert len(rows) == 2
    assert meta["pages"] == meta["declared_pages"] == 1
    assert meta["discovered_links"] == 3
    assert meta["current_count"] == 2
    assert meta["expired_count"] == 1
    assert meta["detail_attempts"] == meta["detail_pages"] == 2
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert "configured_collection_error" not in meta
    assert {row["provider_course_id"] for row in rows} == {
        f"{EDU_PROVIDER}:program:9001",
        f"{EDU_PROVIDER}:program:9002",
    }
    assert all(row["prefer_incoming_provider_course_id"] is True for row in rows)
    assert all(row["branch"] == municipal.GWANGJIN_EDU_BRANCH for row in rows)
    assert all(row["venue_name"].startswith("광진 강의실") for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["municipality_code"] == "1121500000" for row in rows)
    open_row = next(row for row in rows if row["status"] == "OPEN")
    assert open_row["reservation_available"] is True
    assert urlparse(open_row["application_url"]).path.endswith("/applcnt.do")
    scheduled = next(row for row in rows if row["status"] == "SCHEDULED")
    assert scheduled["reservation_available"] is False
    assert "application_url" not in scheduled
    assert scheduled["raw_fields"]["clear_application_url"] is True
    assert len([url for url in edu_site if urlparse(url).path.endswith("/view.do")]) == 2


def _fmcs_item(company: str, class_code: str, status: str, title: str) -> dict[str, Any]:
    return {
        "comcd": company,
        "comnm": f"센터 {company}",
        "class_cd": class_code,
        "class_nm": title,
        "train_stime": "10:00",
        "train_etime": "12:00",
        "course_fee": "10000",
        "train_sdate": "2099-08-01",
        "train_edate": "2099-08-31",
        "status": status,
        "target_age_name": "광진구민",
        "train_day_nm": "월수",
        "capa": "20",
        "reg_person": "4",
        "teacher_name": "광진 강사",
        "category1": "문화강좌",
        "category2": "시민교육",
    }

@pytest.fixture
def fmcs_site(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    companies = [
        {"comcd": "GWANGJIN01", "comnm": "광진구민체육센터"},
        {"comcd": "GWANGJIN03", "comnm": "광진문화예술회관"},
    ]
    by_company = {
        "GWANGJIN01": [
            _fmcs_item("GWANGJIN01", "001", "R", "접수 강좌"),
            _fmcs_item("GWANGJIN01", "002", "W", "예정 강좌"),
        ],
        "GWANGJIN03": [_fmcs_item("GWANGJIN03", "003", "E", "마감 강좌")],
    }
    requested_details: list[str] = []

    def request_json(
        _session: object,
        _root: str,
        endpoint: str,
        params: dict[str, Any],
        _method: str,
        _referer: str,
        _timeout: int,
    ) -> list[dict[str, Any]]:
        if endpoint == "rest/common/company":
            return companies
        assert endpoint == "rest/lecture/list"
        assert params["category_cd"] == ""
        assert params["search_type"] == "%"
        items = [dict(item) for item in by_company[params["company_code"]]]
        for item in items:
            item["total_count"] = len(items)
        return items

    def detail(_session: object, url: str, _timeout: int) -> dict[str, Any]:
        requested_details.append(url)
        query = parse_qs(urlparse(url).query)
        class_code = query["classcd"][0]
        return {
            "title": next(
                item["class_nm"]
                for items in by_company.values()
                for item in items
                if item["class_cd"] == class_code
            ),
            "branch": f"센터 {query['comcd'][0]}",
            "period": "2099-08-01 ~ 2099-08-31",
            "schedule_raw": "10:00 ~ 12:00 / 월수",
            "target": "광진구민",
            "instructor": "광진 강사",
            "application_method_raw": "",
            "fee": "10,000원",
            "capacity": "4 / 20",
            "venue_name": f"강의실 {class_code}",
            "description": f"공식 상세 {class_code}",
            "raw_detail_pairs": {"접수방식": "선착접수"},
        }

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fmcs_http_method", lambda *_args: "get")
    monkeypatch.setattr(municipal, "fmcs_request_json", request_json)
    monkeypatch.setattr(municipal, "fmcs_detail_fields", detail)
    return requested_details


def test_gwangjin_fmcs_company_snapshot_and_official_ids(fmcs_site: list[str]) -> None:
    rows, parser, meta = municipal.collect_from_url(
        _fmcs_target(), timeout=5, max_depth=0, max_pages=10, detail_limit=10
    )

    assert parser == municipal.GWANGJIN_FMCS_PARSER
    assert len(rows) == 3
    assert meta["pages"] == 2
    assert meta["company_totals"] == {"GWANGJIN01": 2, "GWANGJIN03": 1}
    assert meta["total_count"] == 3
    assert meta["detail_attempts"] == meta["detail_pages"] == 3
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert "configured_collection_error" not in meta
    assert {row["provider_course_id"] for row in rows} == {
        f"{FMCS_PROVIDER}:class:GWANGJIN01:001",
        f"{FMCS_PROVIDER}:class:GWANGJIN01:002",
        f"{FMCS_PROVIDER}:class:GWANGJIN03:003",
    }
    assert all(row["prefer_incoming_provider_course_id"] is True for row in rows)
    assert all(row["branch"].startswith("센터 GWANGJIN") for row in rows)
    assert all(row["venue_name"].startswith("강의실 ") for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    open_row = next(row for row in rows if row["status"] == "접수중")
    assert open_row["reservation_available"] is True
    assert parse_qs(urlparse(open_row["application_url"]).query)["type"] == ["R"]
    for row in rows:
        if row is open_row:
            continue
        assert row["reservation_available"] is False
        assert "application_url" not in row
        assert row["raw_fields"]["clear_application_url"] is True
    assert len(fmcs_site) == 3


def test_gwangjin_incomplete_detail_blocks_persistence_and_stale(
    edu_site: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        generated,
        "get_db_connection",
        lambda: (_ for _ in ()).throw(AssertionError("incomplete Gwangjin crawl must not open DB")),
    )
    monkeypatch.setattr(generated, "mark_stale_courses", lambda *args: stale_calls.append(args) or 0)

    result = generated._collect_single_target(
        _edu_target(),
        per_target_limit=0,
        max_depth=0,
        max_pages=5,
        detail_limit=1,
        timeout=5,
    )

    assert result.collection_complete is False
    assert "detail enrichment capped at 1 of 2" in result.report.configured_collection_error
    generated._persist_collection_results(
        [result],
        mark_stale=True,
        max_pages=5,
        per_target_limit=0,
        complete_providers={EDU_PROVIDER},
    )
    assert result.report.saved == 0
    assert result.report.success is False
    assert stale_calls == []


def _yaml_target(path: Path, provider: str) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return next(row for row in document["targets"] if row.get("provider") == provider)


def test_gwangjin_configs_duplicates_and_full_snapshot_contract() -> None:
    edu = _yaml_target(ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml", EDU_PROVIDER)
    fmcs = _yaml_target(ROOT / "config" / "crawl_targets" / "sports_facility.yaml", FMCS_PROVIDER)
    duplicate = _yaml_target(
        ROOT / "config" / "crawl_targets" / "welfare.yaml", "GWANGJIN_WELFARE_VENUE_COURSE"
    )
    for target in (edu, fmcs):
        assert target["collection_category"] == "공공예약"
        assert target["domain_category"] == "교육·강좌"
        assert target["source_group"] == "municipal_reservation"
        assert target["service_group"] == "공공강좌"
        assert target["service_group_policy"] == "locked"
        assert target["full_snapshot_required"] is True
        assert target["municipality_code"] == "1121500000"
    assert duplicate["collection_type"] == "duplicate"
    assert duplicate["crawler_status"] == f"duplicate_url:{EDU_PROVIDER}"
    assert duplicate["duplicate_of"] == EDU_PROVIDER

    assert generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[EDU_PROVIDER] == (
        "--save-db", "--mark-stale", "--per-target-limit", "0",
        "--max-pages", "50", "--detail-limit", "200",
    )
    assert generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[FMCS_PROVIDER] == (
        "--save-db", "--mark-stale", "--per-target-limit", "0",
        "--max-pages", "30", "--detail-limit", "1200",
    )

    overrides = yaml.safe_load(
        (ROOT / "config" / "municipal_integrated_reservation_overrides.yaml").read_text(encoding="utf-8")
    )
    municipality = next(row for row in overrides["municipalities"] if row["code"] == "1121500000")
    assert {candidate["url"] for candidate in municipality["candidates"]} == {
        municipal.GWANGJIN_EDU_LIST_URL,
        municipal.GWANGJIN_FMCS_LIST_URL,
    }
