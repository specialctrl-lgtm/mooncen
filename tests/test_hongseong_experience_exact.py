from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from Crawler import Crawler_MunicipalYaml as router
from Crawler import municipal_hongseong_experience as collector


PROGRAMS = (
    {
        "identity": "201",
        "title": "(8/26, 수)삼원색으로 시작하는 컬러차트",
        "start": "2026-08-26",
        "end": "2026-08-26",
        "venue": "이응노의 집 창작스튜디오 세미나실",
        "content": "삼원색 조색 실습으로 나만의 컬러차트를 만들어 보는 프로그램",
        "status": "OPEN",
        "capacity": "0 / 8",
    },
    {
        "identity": "200",
        "title": "(8/22, 토)삼원색으로 시작하는 컬러차트",
        "start": "2026-08-22",
        "end": "2026-10-22",
        "venue": "이응노의 집 창작스튜디오 세미나실",
        "content": "삼원색 조색 실습으로 나만의 컬러차트를 만들어 보는 프로그램",
        "status": "OPEN",
        "capacity": "1 / 8",
    },
    *(
        {
            "identity": str(identity),
            "title": f"지난 미술 강연 {identity}",
            "start": "2026-07-01",
            "end": "2026-07-01",
            "venue": "고암학술연구실",
            "content": "이응노 작품을 이해하는 강연 프로그램",
            "status": "CLOSED",
            "capacity": "8 / 8",
        }
        for identity in range(199, 189, -1)
    ),
    {
        "identity": "189",
        "title": "이응노의 집 공방프로그램<가죽에 새기는 나만의 트래블 키트>",
        "start": "2026-07-06",
        "end": "2026-08-10",
        "venue": "홍천마을 옥이쓰공방",
        "content": "카드지갑, 여권케이스, 키링을 제작해본다.",
        "status": "CLOSED",
        "capacity": "10 / 10",
    },
    {
        "identity": "188",
        "title": "지난 미술 강연 188",
        "start": "2025-12-01",
        "end": "2025-12-01",
        "venue": "고암학술연구실",
        "content": "이응노 작품을 이해하는 강연 프로그램",
        "status": "CLOSED",
        "capacity": "8 / 8",
    },
    {
        "identity": "187",
        "title": "지난 미술 강연 187",
        "start": "2025-11-01",
        "end": "2025-11-01",
        "venue": "고암학술연구실",
        "content": "이응노 작품을 이해하는 강연 프로그램",
        "status": "CLOSED",
        "capacity": "8 / 8",
    },
)


@dataclass
class _Response:
    url: str
    content: bytes
    status_code: int = 200

    @property
    def headers(self) -> dict[str, str]:
        return {"content-type": "text/html; charset=UTF-8"}

    @property
    def history(self) -> tuple[Any, ...]:
        return ()


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target() -> dict[str, str]:
    return {
        "provider": collector.HONGSEONG_EXPERIENCE_PROVIDER,
        "url": collector.HONGSEONG_EXPERIENCE_URL,
    }


def _page_title() -> str:
    return "이응노의 집 교육신청 &gt; 교육/강좌 &gt; 홍성군 통합예약시스템"


def _list_html(page: int, *, sentinel_nonempty: bool = False) -> bytes:
    if page == 3 and not sentinel_nonempty:
        return f"""
        <!doctype html><html><head><title>{_page_title()}</title></head>
        <body><div id="txt"><div id="edu_lst" class="center">
          등록된 교육프로그램이 없습니다.
        </div></div></body></html>
        """.encode()
    selected = PROGRAMS[(page - 1) * 10 : page * 10]
    if page == 3 and sentinel_nonempty:
        selected = PROGRAMS[:1]
    cards = []
    for program in selected:
        cards.append(
            f"""
            <li class="item"><figure></figure><div class="info_lst type2">
              <strong class="subject">{program['title']}</strong>
              <ul>
                <li class="addr"><em>운영기간</em>{program['start']}~{program['end']}</li>
                <li class="tel"><em>장소</em>{program['venue']}</li>
                <li class="room"><em>프로그램내용</em>{program['content']}</li>
                <li class="tel"><em>문의전화</em>041-630-9245</li>
              </ul>
              <p><a class="btn btn-primary btn-icon"
                href="/prog/educate/02/yeyak/sub01_05/view.do?pageIndex={page}&amp;eduNo={program['identity']}&amp;kind=">자세히보기</a></p>
            </div></li>
            """
        )
    active = min(page, 2)
    return f"""
    <!doctype html><html><head><title>{_page_title()}</title></head><body>
      <div id="txt"><ul id="foodstay_lst">{''.join(cards)}</ul>
        <div class="pagination">
          <li class="page-item{' active' if active == 1 else ''}"><a class="page-link">1</a></li>
          <li class="page-item{' active' if active == 2 else ''}"><a class="page-link">2</a></li>
          <li class="page-item"><a class="page-link" aria-label="last" href="?pageIndex=2">last</a></li>
        </div>
      </div>
    </body></html>
    """.encode()


def _detail_html(identity: str, *, bad_application_path: bool = False) -> bytes:
    program = next(item for item in PROGRAMS if item["identity"] == identity)
    if identity == "201":
        apply_period = "2026-08-03 12:00부터 2026-08-25 17:00까지"
    elif identity == "200":
        apply_period = "2026-08-03 12:00부터 2026-08-21 17:00까지"
    else:
        apply_period = "2026-06-29 09:00부터 2026-07-03 12:00까지"
    if program["status"] == "OPEN":
        path = (
            "/prog/educate/02/yeyak/sub01_05/login.do"
            if bad_application_path
            else "/prog/educate/reserve/02/yeyak/sub01_05/write.do"
        )
        application = (
            f'<a class="btn btn-primary" href="{path}?eduNo={identity}&amp;kind=">'
            "<em>신청하기</em></a>"
        )
    else:
        application = '<span class="btn btn-primary"><em>신청마감</em></span>'
    return f"""
    <!doctype html><html><head><title>{_page_title()}</title></head><body>
      <div id="txt"><div class="foodstaywrap">
        <h2 class="h2">{program['title']}</h2>
        <div id="edu_view"><div class="foodstay_info"><ul class="list-1st">
          <li><strong>운영기간</strong><br>{program['start']} ~ {program['end']}</li>
          <li><strong>접수기간</strong><br>{apply_period}</li>
          <li><strong>장소</strong><br>{program['venue']}</li>
          <li><strong>문의전화</strong><br>041-630-9245</li>
          <li><strong>신청현황</strong><br>{program['capacity']}</li>
        </ul></div></div>
        <h3 class="h3">프로그램내용</h3><div class="edu_vmore2">{program['content']}</div>
        <h3 class="h3">상세정보</h3><div class="edu_vmore2">공개 프로그램 상세</div>
        <p class="text-right">{application}
          <a class="btn btn-default btn-sm btn-list"
             href="/prog/educate/02/yeyak/sub01_05/list.do?pageIndex=1&amp;kind=">목록보기</a>
        </p>
      </div></div>
    </body></html>
    """.encode()


def _collect(
    *,
    detail_limit: int = 10,
    sentinel_nonempty: bool = False,
    bad_application_path: bool = False,
):
    calls: list[str] = []
    session = _Session()

    def fetcher(_session: Any, url: str, _timeout: int) -> _Response:
        calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == collector.HONGSEONG_EXPERIENCE_LIST_PATH:
            page = int(query["pageIndex"][0])
            return _Response(
                url,
                _list_html(page, sentinel_nonempty=sentinel_nonempty),
            )
        identity = query["eduNo"][0]
        return _Response(
            url,
            _detail_html(identity, bad_application_path=bad_application_path),
        )

    rows, parser, meta = collector.collect_hongseong_experience(
        _target(),
        today="2026-08-05",
        timeout=3,
        max_pages=10,
        detail_limit=detail_limit,
        session_factory=lambda: session,
        fetcher=fetcher,
    )
    return rows, parser, meta, calls, session


def test_exact_target_and_public_get_allowlist() -> None:
    assert collector.is_hongseong_experience_target(_target())
    assert not collector.is_hongseong_experience_target(
        {**_target(), "url": collector.HONGSEONG_EXPERIENCE_URL + "?pageIndex=1"}
    )
    assert collector._request_kind(collector.hongseong_experience_list_url(2)) == "list"
    assert collector._request_kind(collector.hongseong_experience_detail_url("201")) == "detail"
    for unsafe in (
        "https://www.hongseong.go.kr/prog/educate/reserve/02/yeyak/sub01_05/write.do?eduNo=201&kind=",
        "https://www.hongseong.go.kr/prog/educate/02/yeyak/sub01_05/login.do",
        "https://www.hongseong.go.kr/prog/myInfo/yeyak/exprn_lnbns/sub06_02/list.do",
        "https://www.hongseong.go.kr/cmm/fms/FileDown.do?atchFileId=1",
        "https://www.hongseong.go.kr/prog/educate/02/yeyak/sub01_05/view.do?eduNo=201&download=1",
    ):
        with pytest.raises(collector.HongseongExperienceContractError):
            collector._request_kind(unsafe)


def test_complete_fixture_returns_open_and_closed_and_quarantines_date_anomaly() -> None:
    rows, parser, meta, calls, session = _collect()

    assert parser == collector.HONGSEONG_EXPERIENCE_PARSER
    assert [row["raw_fields"]["identity"] for row in rows] == ["201", "189"]
    assert [row["status"] for row in rows] == ["OPEN", "CLOSED"]
    assert [row["reservation_available"] for row in rows] == [True, False]
    assert all(row["application_url"] == "" for row in rows)
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert meta["source_total"] == 15
    assert meta["current_source_count"] == meta["current_experience_count"] == 3
    assert meta["experience_source_count"] == 3
    assert meta["expired_count"] == 12
    assert meta["page_counts"] == {1: 10, 2: 5}
    assert meta["sentinel_page"] == 3 and meta["sentinel_count"] == 0
    assert meta["boundary_rechecks"] == 3
    assert meta["detail_verified"] == 3
    assert meta["title_date_anomaly_count"] == 1
    assert meta["title_date_anomalies"] == [
        {
            "identity": "200",
            "reason": "single_title_round_date_conflicts_with_structured_date_range",
            "source_status": "신청하기",
        }
    ]
    assert meta["verified_source_status_counts"] == {"OPEN": 2, "CLOSED": 1}
    assert meta["status_counts"] == {"OPEN": 1, "CLOSED": 1}
    assert meta["list_requests"] == 6 and meta["detail_requests"] == 3
    assert meta["logical_requests"] == 9
    assert meta["application_control_count"] == 2
    assert meta["application_url_persisted_count"] == 0
    assert meta["reservation_available_count"] == 1
    assert meta["snapshot_complete"] is meta["details_complete"] is True
    assert session.closed is True
    assert not any(
        marker in url.lower()
        for url in calls
        for marker in (
            "write.do",
            "login",
            "member",
            "applicant",
            "myinfo",
            "filedown",
            "download",
        )
    )
    for key in (
        "application_endpoint_requests",
        "login_endpoint_requests",
        "member_endpoint_requests",
        "applicant_endpoint_requests",
        "identity_endpoint_requests",
        "attachment_endpoint_requests",
        "download_endpoint_requests",
        "pii_endpoint_requests",
    ):
        assert meta[key] == 0


def test_contract_drift_and_detail_cap_are_atomic() -> None:
    rows, _, meta, _, session = _collect(sentinel_nonempty=True)
    assert rows == [] and meta["snapshot_complete"] is False
    assert "sentinel changed" in meta["errors"][0]
    assert session.closed is True

    rows, _, meta, _, _ = _collect(bad_application_path=True)
    assert rows == [] and "application identity/path changed" in meta["errors"][0]

    rows, _, meta, calls, _ = _collect(detail_limit=2)
    assert rows == [] and "detail_limit" in meta["errors"][0]
    assert not any(collector.HONGSEONG_EXPERIENCE_DETAIL_PATH in url for url in calls)


def test_router_dispatches_exact_target_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    def fake_collect(target: Any, **kwargs: Any):
        calls.append((target, kwargs))
        return [
            {"provider": collector.HONGSEONG_EXPERIENCE_PROVIDER}
        ], "fixture", {"ok": True}

    monkeypatch.setattr(collector, "collect_hongseong_experience", fake_collect)
    target = router.CrawlTarget(
        provider=collector.HONGSEONG_EXPERIENCE_PROVIDER,
        name="홍성군 이응노의 집 체험",
        branch="이응노의 집",
        url=collector.HONGSEONG_EXPERIENCE_URL,
        source="test",
        priority=1,
        region="충청남도 홍성군",
        extra={},
    )
    rows, parser, meta = router.collect_from_url(
        target, timeout=3, max_depth=0, max_pages=10, detail_limit=10
    )
    assert rows and parser == "fixture" and meta == {"ok": True}
    assert len(calls) == 1
    assert calls[0][1]["session_factory"] is router.session


def test_single_yaml_target_and_operational_linkage() -> None:
    root = Path(__file__).resolve().parents[1]
    targets = yaml.safe_load(
        (root / "config/crawl_targets/public_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )
    matches = [
        item
        for item in targets["targets"]
        if item.get("provider") == collector.HONGSEONG_EXPERIENCE_PROVIDER
    ]
    assert len(matches) == 1
    assert matches[0]["url"] == collector.HONGSEONG_EXPERIENCE_URL
    assert matches[0]["crawler_module"] == "Crawler.municipal_hongseong_experience"
    assert matches[0]["ops_scopes"] == ["experience"]

    operational = yaml.safe_load(
        (root / "config/municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert any(
        item.get("provider") == collector.HONGSEONG_EXPERIENCE_PROVIDER
        and item.get("row_count") == 2
        for item in operational["entries"]
    )
