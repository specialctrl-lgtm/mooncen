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


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _target(provider: str, url: str, branch: str) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=provider,
        name=branch,
        branch=branch,
        url=url,
        source="test",
        priority=1,
        region=municipal.SDM_MUNICIPALITY_NAME,
    )


def _apply_list_row(
    seq: str,
    title: str,
    scope: str,
    *,
    application: bool = False,
) -> str:
    application_control = (
        f'<button onclick="goYeyakApp(\'{seq}\')">신청</button>' if application else ""
    )
    return (
        "<tr>"
        "<td>1</td>"
        f"<td><a href=\"javascript:goView('{seq}')\">{title}</a></td>"
        "<td>3/20/5</td>"
        f"<td><img alt=\"{scope}\"></td>"
        "<td>2098.12.01 09:00 ~ 2098.12.31 18:00</td>"
        "<td>교육</td>"
        f"<td>온라인 {application_control}</td>"
        "</tr>"
    )


def _apply_detail(seq: str, title: str, scope: str, *, application: bool) -> BeautifulSoup:
    control = '<a href="javascript:goNext();">신청하기</a>' if application else ""
    return _soup(
        "<html><body><table><tbody>"
        f"<tr><th>제목</th><td>{title}</td></tr>"
        f"<tr><th>접수상태</th><td>{scope} 2098.12.01 09:00 ~ 2098.12.31 18:00</td></tr>"
        "<tr><th>신청/모집/예비인원</th><td>3 / 20 / 5</td></tr>"
        "<tr><th>신청대상</th><td>서대문구민</td></tr>"
        f"<tr><th>장소</th><td>서대문 교육장 {seq}</td></tr>"
        "<tr><th>비용</th><td>무료</td></tr>"
        "<tr><th>문의처</th><td>02-330-1234</td></tr>"
        "<tr><th>상세내용</th><td>공식 교육 프로그램 안내입니다. "
        "운영일시: 2099년 1월 10일 ~ 2099년 2월 20일 매주 화요일 10:00</td></tr>"
        f"</tbody></table>{control}</body></html>"
    )


def test_sdm_integrated_application_collects_current_scopes_and_excludes_only_owned_non_courses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target(
        municipal.SDM_APPLY_PROVIDER,
        municipal.SDM_APPLY_LIST_URL,
        "서대문구 통합신청",
    )
    pages = {
        "신청": _soup(
            "<html><body><table><tbody>"
            + _apply_list_row("900", "서대문 열린 교육", "신청", application=True)
            + _apply_list_row("893", "공식 테스트", "신청")
            + _apply_list_row("891", "판매 행사", "신청")
            + "</tbody></table></body></html>"
        ),
        "접수기간전": _soup(
            "<html><body><table><tbody>"
            + _apply_list_row("892", "서대문 예정 교육", "접수기간전")
            + "</tbody></table></body></html>"
        ),
    }
    details = {
        "900": _apply_detail("900", "서대문 열린 교육", "신청", application=True),
        "892": _apply_detail("892", "서대문 예정 교육", "접수기간전", application=False),
    }

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "sdm_apply_post_soup",
        lambda _session, scope, page, timeout: pages[scope],
    )

    def fetch_details(urls: list[str], timeout: int, *, workers: int = 4):
        assert timeout > 0 and workers == 4
        soups: dict[int, BeautifulSoup] = {}
        for index, url in enumerate(urls):
            seq = parse_qs(urlparse(url).query)["seq"][0]
            soups[index] = details[seq]
        return soups, {}

    monkeypatch.setattr(municipal, "sdm_fetch_soups_parallel", fetch_details)

    rows, parser, meta = municipal.collect_from_url(
        target,
        timeout=5,
        max_depth=0,
        max_pages=10,
        detail_limit=10,
    )

    assert parser == municipal.SDM_APPLY_PARSER
    assert [row["provider_course_id"] for row in rows] == [
        f"{municipal.SDM_APPLY_PROVIDER}:form:900",
        f"{municipal.SDM_APPLY_PROVIDER}:form:892",
    ]
    assert [row["status"] for row in rows] == ["OPEN", "SCHEDULED"]
    assert rows[0]["reservation_available"] is True
    assert rows[0]["application_url"].endswith("mode=appRegFrm&formSeq=900")
    assert rows[1]["reservation_available"] is False
    assert "application_url" not in rows[1]
    assert {row["branch"] for row in rows} == {
        "서대문 교육장 900",
        "서대문 교육장 892",
    }
    assert all(row["collection_category"] == "공공예약" for row in rows)
    assert all(row["domain_category"] == "교육·강좌" for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["municipality_code"] == "1141000000" for row in rows)
    assert meta["excluded_counts"] == {
        "official_test_record": 1,
        "non_education_sales_event": 1,
    }
    assert meta["snapshot_complete"] is True
    assert meta["source_cap_reached"] is False
    assert "configured_collection_error" not in meta


def _lifelong_detail(idx: str, title: str, *, ended: bool, application: bool) -> BeautifulSoup:
    end = "2020-02-20" if ended else "2099-02-20"
    apply_control = "<a>신청하기</a>" if application else ""
    items = {
        "교육기간": f"2099-01-10 ~ {end}",
        "교육장소": f"서대문 평생학습관 {idx}",
        "교육장명": f"강의실 {idx}",
        "접수기간": "2098-12-01 ~ 2098-12-31",
        "교육요일": "화 10:00~12:00",
        "교육대상": "성인",
        "수강료": "무료",
        "문의전화": "02-330-5678",
        "강사명": "서대문 강사",
        "신청방법": "온라인",
    }
    fields = "".join(
        f'<li class="item"><strong class="t">{key}</strong><span>{value}</span></li>'
        for key, value in items.items()
    )
    return _soup(
        f"<html><body><h2>{title}</h2><ul>{fields}</ul>{apply_control}"
        f'<div class="group"><h3>강의내용</h3>{title} 공식 강의 내용 재료비: 없음</div>'
        "</body></html>"
    )


def test_sdm_lifelong_latest_scope_keeps_only_unended_courses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target(
        municipal.SDM_LIFELONG_PROVIDER,
        municipal.SDM_LIFELONG_LIST_URL,
        "서대문구 평생학습관",
    )
    list_soup = _soup(
        "<html><body><p class='total'>전체 건수 2건 현재페이지 : 1 / 1</p>"
        "<table><tbody>"
        "<tr><td>2</td><td>성인</td><td><a href='lectureInfoView.do?idx=200'>현재 강좌</a></td>"
        "<td>2098.12.01 ~ 2098.12.31</td><td>1 / 20</td><td>신청</td></tr>"
        "<tr><td>1</td><td>성인</td><td><a href='lectureInfoView.do?idx=100'>종료 강좌</a></td>"
        "<td>2019.12.01 ~ 2019.12.31</td><td>20 / 20</td><td>접수완료</td></tr>"
        "</tbody></table></body></html>"
    )
    detail_by_idx = {
        "200": _lifelong_detail("200", "현재 강좌", ended=False, application=True),
        "100": _lifelong_detail("100", "종료 강좌", ended=True, application=False),
    }

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", lambda *_args, **_kwargs: list_soup)

    def fetch_details(urls: list[str], timeout: int, *, workers: int = 4):
        return {
            index: detail_by_idx[parse_qs(urlparse(url).query)["idx"][0]]
            for index, url in enumerate(urls)
        }, {}

    monkeypatch.setattr(municipal, "sdm_fetch_soups_parallel", fetch_details)

    rows, parser, meta = municipal.collect_sdm_lifelong_courses(
        target,
        timeout=5,
        max_pages=1,
        detail_limit=2,
    )

    assert parser == municipal.SDM_LIFELONG_PARSER
    assert len(rows) == 1
    assert rows[0]["provider_course_id"].endswith(":lecture:200")
    assert rows[0]["branch"] == "서대문 평생학습관 200"
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["reservation_available"] is True
    assert meta["total_count"] == 2
    assert meta["current_count"] == 1
    assert meta["expired_count"] == 1
    assert meta["snapshot_complete"] is True
    assert "configured_collection_error" not in meta


def _sscmc_api_item(
    comcd: str,
    branch: str,
    class_cd: str,
    title: str,
    *,
    total: int,
) -> dict[str, Any]:
    return {
        "comcd": comcd,
        "comnm": branch,
        "class_cd": class_cd,
        "class_nm": title,
        "status": "R",
        "total_count": total,
        "train_stime": "10:00",
        "train_etime": "11:00",
        "course_fee": "10,000",
        "target_age_name": "성인",
        "train_day_nm": "화",
        "capa": "20",
        "reg_person": "2",
        "teacher_name": "공단 강사",
        "category1": "교육",
        "category2": "문화강좌",
    }


def _sscmc_detail(
    comcd: str,
    class_cd: str,
    branch: str,
    title: str,
    *,
    online_forbidden: bool,
) -> BeautifulSoup:
    warning = "방문 접수만 가능합니다." if online_forbidden else "온라인 신청 가능합니다."
    return _soup(
        "<html><body><div class='status_box'><span>신청기간 "
        "<em>2098-12-01 09:00 ~ 2098-12-31 18:00</em></span></div>"
        "<form id='form_lecture_reg'>"
        f"<input name='comcd' value='{comcd}'>"
        f"<input name='classcd' value='{class_cd}'>"
        "<input name='type' value='R'><input name='status' value='R'>"
        "</form><table><tbody>"
        f"<tr><th>센터명</th><td>{branch}</td></tr>"
        f"<tr><th>강좌명</th><td>{title}</td></tr>"
        "<tr><th>교육기간</th><td>2099-01-01 ~ 2099-03-31</td></tr>"
        "<tr><th>교육시간</th><td>화 / 10:00 ~ 11:00</td></tr>"
        "<tr><th>교육장소</th><td>문화강좌실</td></tr>"
        "<tr><th>교육대상</th><td>성인</td></tr>"
        "<tr><th>강사명</th><td>공단 강사</td></tr>"
        "<tr><th>접수방법</th><td>선착접수</td></tr>"
        "<tr><th>수강료</th><td>10,000원</td></tr>"
        "<tr><th>잔여인원/정원</th><td>18 / 20</td></tr>"
        "</tbody></table>"
        f"<dl><dt>강좌소개</dt><dd>{warning}</dd></dl>"
        "<dl><dt>강좌내용</dt><dd>공식 교육 강좌입니다.</dd></dl>"
        "</body></html>"
    )


def test_sscmc_compound_identity_branch_ownership_and_online_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target(municipal.SSCMC_PROVIDER, municipal.SSCMC_ROOT_URL, "서대문도시관리공단")
    company_rows = [
        {"comcd": code, "comnm": value[0]}
        for code, value in municipal.SSCMC_COMPANIES.items()
    ]
    items = {
        "SSCMC01": [
            _sscmc_api_item(
                "SSCMC01",
                "서대문문화체육회관",
                "00001",
                "온라인 공단강좌",
                total=1,
            )
        ],
        "SSCMC02": [
            _sscmc_api_item(
                "SSCMC02",
                "북아현문화체육센터",
                "00001",
                "방문 공단강좌",
                total=1,
            )
        ],
        "SSCMC03": [],
    }
    details = {
        ("SSCMC01", "00001"): _sscmc_detail(
            "SSCMC01",
            "00001",
            "서대문문화체육회관",
            "온라인 공단강좌",
            online_forbidden=False,
        ),
        ("SSCMC02", "00001"): _sscmc_detail(
            "SSCMC02",
            "00001",
            "북아현문화체육센터",
            "방문 공단강좌",
            online_forbidden=True,
        ),
    }

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fmcs_http_method", lambda *_args: "get")

    def request_json(
        _session: object,
        root_url: str,
        endpoint: str,
        params: dict[str, Any],
        method: str,
        referer: str,
        timeout: int,
    ) -> list[dict[str, Any]]:
        assert root_url == municipal.SSCMC_ROOT_URL
        if endpoint == "rest/common/company":
            return company_rows
        assert endpoint == "rest/lecture/list"
        return items[str(params["company_code"])]

    monkeypatch.setattr(municipal, "fmcs_request_json", request_json)

    def fetch_details(urls: list[str], timeout: int, *, workers: int = 4):
        soups: dict[int, BeautifulSoup] = {}
        for index, url in enumerate(urls):
            query = parse_qs(urlparse(url).query)
            soups[index] = details[(query["comcd"][0], query["classcd"][0])]
        return soups, {}

    monkeypatch.setattr(municipal, "sdm_fetch_soups_parallel", fetch_details)

    rows, parser, meta = municipal.collect_sscmc_courses(
        target,
        timeout=5,
        max_pages=3,
        detail_limit=2,
    )

    assert parser == municipal.SSCMC_PARSER
    assert {row["provider_course_id"] for row in rows} == {
        f"{municipal.SSCMC_PROVIDER}:SSCMC01:00001",
        f"{municipal.SSCMC_PROVIDER}:SSCMC02:00001",
    }
    assert {row["branch"] for row in rows} == {
        "서대문문화체육회관",
        "북아현문화체육센터",
    }
    online = next(row for row in rows if row["branch_code"] == "SSCMC01")
    offline = next(row for row in rows if row["branch_code"] == "SSCMC02")
    assert online["reservation_available"] is True
    assert online["application_url"] == online["raw_url"]
    assert offline["reservation_available"] is False
    assert "application_url" not in offline
    assert meta["company_totals"] == {"SSCMC01": 1, "SSCMC02": 1, "SSCMC03": 0}
    assert meta["snapshot_complete"] is True
    assert meta["reservation_discovery_links"] == 1
    assert "configured_collection_error" not in meta


def test_sdm_terminal_page_declaration_includes_plain_text_current_page() -> None:
    soup = _soup(
        "<div class='paging'>"
        "<a href='javascript:goPage(1)'>1</a>"
        "<a href='javascript:goPage(2)'>2</a>"
        "<a href='javascript:goPage(3)'>3</a>"
        "<a href='javascript:goPage(4)'>4</a><strong>5</strong>"
        "</div>"
    )
    assert municipal.sdm_apply_declared_pages(soup, current_page=5) == 5


def test_seodaemun_targets_are_unique_locked_education_owners() -> None:
    expected = {
        municipal.SDM_APPLY_PROVIDER: municipal.SDM_APPLY_LIST_URL,
        municipal.SDM_LIFELONG_PROVIDER: municipal.SDM_LIFELONG_LIST_URL,
        municipal.SSCMC_PROVIDER: municipal.SSCMC_ROOT_URL,
    }
    target_rows: list[dict[str, Any]] = []
    for path in sorted((ROOT / "config" / "crawl_targets").glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        target_rows.extend(row for row in document.get("targets") or [] if isinstance(row, dict))

    for provider, url in expected.items():
        matches = [row for row in target_rows if row.get("provider") == provider]
        assert len(matches) == 1
        row = matches[0]
        assert row["url"] == url
        assert row["crawler_status"] == "ready"
        assert row["collection_category"] == "공공예약"
        assert row["domain_category"] == "교육·강좌"
        assert row["source_group"] == "municipal_reservation"
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["municipality_code"] == "1141000000"
        assert row["municipality_full_name"] == municipal.SDM_MUNICIPALITY_NAME
        assert row["full_snapshot_required"] is True

        arguments = list(generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[provider])
        assert arguments[:4] == ["--save-db", "--mark-stale", "--per-target-limit", "0"]
        parsed = generated.parse_args(["--provider", provider, *arguments])
        assert parsed.save_db is True
        assert parsed.mark_stale is True
        assert parsed.per_target_limit == 0
        assert parsed.allow_partial_save is False

    normalized = {
        municipal.SDM_APPLY_PROVIDER: municipal.is_sdm_apply_target(
            _target(municipal.SDM_APPLY_PROVIDER, municipal.SDM_APPLY_LIST_URL, "서대문구 통합신청")
        ),
        municipal.SDM_LIFELONG_PROVIDER: municipal.is_sdm_lifelong_target(
            _target(municipal.SDM_LIFELONG_PROVIDER, municipal.SDM_LIFELONG_LIST_URL, "서대문구 평생학습관")
        ),
        municipal.SSCMC_PROVIDER: municipal.is_sscmc_target(
            _target(municipal.SSCMC_PROVIDER, municipal.SSCMC_ROOT_URL, "서대문도시관리공단")
        ),
    }
    assert all(normalized.values())
