from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from Crawler import Crawler_MunicipalYaml as router
from Crawler import municipal_geumsan_experience as collector


ACTIVE = (
    {
        "identity": "4972",
        "list_title": "[특강] 걱정인형 만들기_내 걱정...",
        "title": "[특강] 걱정인형 만들기_내 걱정을 가져가!",
        "start": "2026-08-13",
        "end": "2026-08-13",
        "time": "14:00~15:00",
        "target": "청소년 > 기타(초3~중3)",
        "schedule": "2026. 8. 13.(목)",
        "status": "접수중",
        "current": 5,
        "total": 12,
        "wait_current": 0,
        "wait_total": 0,
    },
    {
        "identity": "4968",
        "list_title": "[특강] 꺼먹살이 키링 DIY",
        "title": "[특강] 꺼먹살이 키링 DIY",
        "start": "2026-08-11",
        "end": "2026-08-11",
        "time": "10:00~12:00",
        "target": "청소년 > 기타(초5 ~ 중3)",
        "schedule": "2026. 8. 11.(화)",
        "status": "대기접수",
        "current": 12,
        "total": 12,
        "wait_current": 0,
        "wait_total": 0,
    },
    {
        "identity": "4971",
        "list_title": "[특강] 디퓨져 DIY_아이스팩 재활용",
        "title": "[특강] 디퓨져 DIY_아이스팩 재활용",
        "start": "2026-08-13",
        "end": "2026-08-13",
        "time": "10:30~12:00",
        "target": "청소년 > 기타(초4~초6)",
        "schedule": "2026. 8. 13.(목)",
        "status": "접수중",
        "current": 5,
        "total": 12,
        "wait_current": 0,
        "wait_total": 0,
    },
    {
        "identity": "4969",
        "list_title": "[특강] 스칸디아모스 DIY",
        "title": "[특강] 스칸디아모스 DIY",
        "start": "2026-08-12",
        "end": "2026-08-12",
        "time": "14:00~15:00",
        "target": "청소년 > 기타(초1~초6)",
        "schedule": "2026. 8. 12.(수)",
        "status": "대기접수",
        "current": 12,
        "total": 12,
        "wait_current": 2,
        "wait_total": 5,
    },
    {
        "identity": "4973",
        "list_title": "[특강] 태극기 부채만들기_8·15 ...",
        "title": "[특강] 태극기 부채만들기_8·15 광복절 기념",
        "start": "2026-08-14",
        "end": "2026-08-14",
        "time": "11:00~12:00",
        "target": "청소년 > 기타(초1~초6)",
        "schedule": "2026. 8. 14.(금)",
        "status": "접수중",
        "current": 1,
        "total": 12,
        "wait_current": 0,
        "wait_total": 0,
    },
)

CURRENT_NON_EXPERIENCE = tuple(
    {
        "identity": str(4900 - offset),
        "list_title": f"2026년 하반기 일반 교육 {offset + 1}반",
        "title": f"2026년 하반기 일반 교육 {offset + 1}반",
        "start": "2026-08-01",
        "end": "2026-12-31",
        "time": "18:00~19:00",
        "target": "청소년 > 기타",
        "schedule": "매주 수요일",
        "status": "접수마감",
        "current": 12,
        "total": 12,
        "wait_current": 0,
        "wait_total": 0,
    }
    for offset in range(18)
)

EXPIRED = tuple(
    {
        "identity": str(4800 - offset),
        "list_title": f"지난 일반 교육 {offset + 1}반",
        "title": f"지난 일반 교육 {offset + 1}반",
        "start": "2025-01-01",
        "end": "2026-07-31",
        "time": "18:00~19:00",
        "target": "청소년 > 기타",
        "schedule": "매주 수요일",
        "status": "접수마감",
        "current": 12,
        "total": 12,
        "wait_current": 0,
        "wait_total": 0,
    }
    for offset in range(162)
)

PROGRAMS = ACTIVE + CURRENT_NON_EXPERIENCE + EXPIRED
PROGRAM_BY_ID = {program["identity"]: program for program in PROGRAMS}

assert len(PROGRAMS) == 185
assert len(PROGRAM_BY_ID) == 185


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
        "provider": collector.GEUMSAN_EXPERIENCE_PROVIDER,
        "url": collector.GEUMSAN_EXPERIENCE_URL,
    }


def _list_html(page: int, *, clamp_mutation: str = "") -> bytes:
    observed = min(page, 19)
    selected = list(PROGRAMS[(observed - 1) * 10 : observed * 10])
    if page == 20 and clamp_mutation:
        selected[0] = dict(selected[0])
        if clamp_mutation == "identity":
            selected[0]["identity"] = "9999"
        elif clamp_mutation == "title":
            selected[0]["list_title"] += " 변경"
        else:
            raise AssertionError(clamp_mutation)
    cards: list[str] = []
    for program in selected:
        state = "교육대기" if program in ACTIVE else (
            "교육중" if program in CURRENT_NON_EXPERIENCE else "교육종료"
        )
        cards.append(
            f"""
            <a href="/site/youthcenter/html/sub02/0202.html?mode=V&amp;mng_no={program['identity']}">
              <div class="col"><div class="inner">
                <div class="accept accept02"><span>{program['status']}</span><em>인터넷</em></div>
                <div class="in_top">
                  <div class="cate cate01"><span></span>문화예술</div>
                  <div class="tit">{program['list_title']} <span class="cond cond2">{state}</span></div>
                </div>
                <div class="list_con_w"><ul class="list_con">
                  <li><span>운영주체<em> : </em></span><em>{collector.GEUMSAN_EXPERIENCE_BRANCH}</em></li>
                  <li><span>교육기간<em> : </em></span><em><b>{program['start']}</b><b> ~ {program['end']}</b></em></li>
                  <li><span>교육시간<em> : </em></span><em>{program['time']}</em></li>
                  <li><span>접수기간<em> : </em></span><em><b>2026-07-31 10:00</b><b> ~ 2026-08-07 23:59</b></em></li>
                  <li><span>신청/정원<em> : </em></span><em>{program['current']}명/{program['total']}명</em></li>
                  <li><span>교육장소<em> : </em></span><em>{collector.GEUMSAN_EXPERIENCE_BRANCH}</em></li>
                  <li><span>교육대상<em> : </em></span><em>{program['target']}</em></li>
                  <li><span>교육주기<em> : </em></span><em>{program['schedule']}</em></li>
                </ul></div>
              </div></div>
            </a>
            """
        )
    return f"""
    <!doctype html><html><head><title>
      금산군 목록 &gt; 프로그램신청 &gt; 상설프로그램 &gt; 금산군 청소년수련관
    </title></head><body><div id="txt">
      <div class="program_con">{''.join(cards)}</div>
      <ul class="pagination">
        <li class="active"><a class="page-link" href="?&amp;GotoPage={observed}">{observed}</a></li>
        <li><a class="page-link" aria-label="last" href="?&amp;GotoPage=19">last</a></li>
      </ul>
    </div></body></html>
    """.encode()


def _detail_html(identity: str, *, bad_application_path: bool = False) -> bytes:
    program = PROGRAM_BY_ID[identity]
    state = "교육대기"
    waitlist = (
        f"<em>( 대기 : {program['wait_current']}명 / {program['wait_total']}명)</em>"
        if program["wait_total"]
        else "<em></em>"
    )
    application_path = (
        "/youthcenter/html/sub02/0202.html"
        if not bad_application_path
        else "/youthcenter/html/sub02/login.html"
    )
    return f"""
    <!doctype html><html><head><title>
      금산군 보기 &gt; 프로그램신청 &gt; 상설프로그램 &gt; 금산군 청소년수련관
    </title></head><body><div id="txt">
      <div class="program_con program_view"><div class="col"><div class="inner">
        <div class="accept accept02"><span>{program['status']}</span><em>인터넷</em></div>
        <div class="in_top">
          <div class="cate cate01"><span></span>문화예술</div>
          <div class="tit">{program['title']} <span class="cond cond2">{state}</span></div>
        </div>
        <div class="list_con_w">
          <ul class="list_con">
            <li><span>운영주체</span><em>{collector.GEUMSAN_EXPERIENCE_BRANCH}</em></li>
            <li><span>교육기간</span><em><b>{program['start']}</b><b> ~ {program['end']}</b></em></li>
            <li><span>접수기간</span><em><b>2026-07-31 10:00</b><b> ~ 2026-08-07 23:59</b></em></li>
            <li><span>교육장소</span><em>{collector.GEUMSAN_EXPERIENCE_BRANCH}</em></li>
            <li><span>교육주기</span><em>{program['schedule']}</em></li>
          </ul>
          <ul class="list_con">
            <li><span>교육대상</span><em>{program['target']}</em></li>
            <li><span>교육시간</span><em><b>{program['time']}</b></em></li>
            <li><span>신청/정원</span><em>{program['current']}명/{program['total']}명</em>{waitlist}</li>
            <li><span>문의</span><a href="tel:041-750-4170"><em>041-750-4170</em></a></li>
            <li><span>신청방법</span><em>인터넷</em></li>
          </ul>
        </div>
      </div></div></div>
      <div class="table-responsive"><table class="table"><tbody>
        <tr><td class="td_row">담당강사</td><td></td><td class="td_row">수강료</td><td>무료</td></tr>
        <tr><td class="td_row">강좌 상세설명</td><td colspan="3">금산군 청소년수련관에서 여름특강으로 진행하는 프로그램입니다.</td></tr>
      </tbody></table></div>
      <div class="text-right mt_30">
        <a class="btn btn-primary btn-sm" href="{application_path}?edu_mng_no={identity}&amp;mode=W">강좌신청</a>
        <a class="btn btn-default btn-sm" href="?">목록</a>
      </div>
    </div></body></html>
    """.encode()


def _collect(
    *,
    max_pages: int = 20,
    detail_limit: int = 5,
    clamp_mutation: str = "",
    bad_application_path: bool = False,
):
    calls: list[str] = []
    session = _Session()

    def fetcher(_session: Any, url: str, _timeout: int) -> _Response:
        calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == collector.GEUMSAN_EXPERIENCE_LIST_PATH:
            page = int(query["GotoPage"][0])
            return _Response(url, _list_html(page, clamp_mutation=clamp_mutation))
        identity = query["mng_no"][0]
        return _Response(
            url,
            _detail_html(identity, bad_application_path=bad_application_path),
        )

    rows, parser, meta = collector.collect_geumsan_experience(
        _target(),
        today="2026-08-05",
        timeout=3,
        max_pages=max_pages,
        detail_limit=detail_limit,
        session_factory=lambda: session,
        fetcher=fetcher,
    )
    return rows, parser, meta, calls, session


def test_exact_target_and_public_get_allowlist() -> None:
    assert collector.is_geumsan_experience_target(_target())
    assert not collector.is_geumsan_experience_target(
        {**_target(), "url": collector.GEUMSAN_EXPERIENCE_URL + "?GotoPage=1"}
    )
    assert collector._request_kind(collector.geumsan_experience_list_url(19)) == "list"
    assert collector._request_kind(collector.geumsan_experience_detail_url("4972")) == "detail"
    for unsafe in (
        "https://www.geumsan.go.kr/youthcenter/html/sub02/0202.html?edu_mng_no=4972&mode=W",
        "https://www.geumsan.go.kr/site/youthcenter/html/sub02/login.html",
        "https://www.geumsan.go.kr/member/login.html",
        "https://www.geumsan.go.kr/applicant/list.html",
        "https://www.geumsan.go.kr/cmm/fms/FileDown.do?atchFileId=1",
        "https://www.geumsan.go.kr/site/youthcenter/html/sub02/0202.html?mode=V&mng_no=4972&download=1",
    ):
        with pytest.raises(collector.GeumsanExperienceContractError):
            collector._request_kind(unsafe)


def test_complete_185_23_5_fixture_and_clamp_sentinel_substitute() -> None:
    rows, parser, meta, calls, session = _collect()

    assert parser == collector.GEUMSAN_EXPERIENCE_PARSER
    assert [row["raw_fields"]["identity"] for row in rows] == [
        "4972",
        "4968",
        "4971",
        "4969",
        "4973",
    ]
    assert all(row["status"] == "OPEN" for row in rows)
    assert all(row["reservation_available"] is True for row in rows)
    assert all(row["application_url"] == "" for row in rows)
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert next(
        row for row in rows if row["raw_fields"]["identity"] == "4969"
    )["waitlist_current"] == 2
    assert meta["source_total"] == 185
    assert meta["current_future_count"] == meta["current_source_count"] == 23
    assert meta["current_experience_count"] == meta["returned_count"] == 5
    assert meta["excluded_non_experience_current_count"] == 18
    assert meta["expired_count"] == 162
    assert meta["declared_last_page"] == meta["data_pages"] == 19
    assert meta["page_counts"] == {**{page: 10 for page in range(1, 19)}, 19: 5}
    assert meta["post_last_clamp_page"] == 20
    assert meta["post_last_clamp_observed_page"] == 19
    assert meta["post_last_clamp_count"] == 5
    assert meta["clamp_sentinel_substitute"] is True
    assert meta["clamp_identity_sequence_verified"] is True
    assert meta["clamp_rowset_verified"] is True
    assert meta["boundary_rechecks"] == 3
    assert meta["list_requests"] == 23
    assert meta["detail_requests"] == 5
    assert meta["logical_requests"] == 28
    assert meta["detail_verified"] == 5
    assert meta["application_control_count"] == 5
    assert meta["application_url_persisted_count"] == 0
    assert meta["reservation_available_count"] == 5
    assert meta["snapshot_complete"] is meta["details_complete"] is True
    assert session.closed is True
    assert not any(
        marker in url.lower()
        for url in calls
        for marker in (
            "mode=w",
            "edu_mng_no",
            "login",
            "auth",
            "member",
            "applicant",
            "filedown",
            "download",
        )
    )
    for key in (
        "application_endpoint_requests",
        "login_endpoint_requests",
        "auth_endpoint_requests",
        "member_endpoint_requests",
        "applicant_endpoint_requests",
        "identity_endpoint_requests",
        "file_endpoint_requests",
        "attachment_endpoint_requests",
        "download_endpoint_requests",
        "post_requests",
        "pii_endpoint_requests",
    ):
        assert meta[key] == 0


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("identity", "identity sequence differs"),
        ("title", "row set differs"),
    ),
)
def test_page20_clamp_must_match_page19_identity_and_rows(
    mutation: str, message: str
) -> None:
    rows, _, meta, calls, session = _collect(clamp_mutation=mutation)
    assert rows == [] and meta["snapshot_complete"] is False
    assert message in meta["errors"][0]
    assert not any(collector.GEUMSAN_EXPERIENCE_DETAIL_PATH in url for url in calls)
    assert session.closed is True


def test_detail_contract_and_caps_fail_atomically_without_application_get() -> None:
    rows, _, meta, calls, _ = _collect(bad_application_path=True)
    assert rows == [] and "application control identity/path changed" in meta["errors"][0]
    assert not any("mode=W" in url or "edu_mng_no" in url for url in calls)

    rows, _, meta, calls, _ = _collect(detail_limit=4)
    assert rows == [] and "detail_limit" in meta["errors"][0]
    assert not any(collector.GEUMSAN_EXPERIENCE_DETAIL_PATH in url for url in calls)

    rows, _, meta, calls, _ = _collect(max_pages=19)
    assert rows == [] and "clamp sentinel substitute" in meta["errors"][0]
    assert len(calls) == 1


def test_router_dispatches_exact_target_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    def fake_collect(target: Any, **kwargs: Any):
        calls.append((target, kwargs))
        return [
            {"provider": collector.GEUMSAN_EXPERIENCE_PROVIDER}
        ], "fixture", {"ok": True}

    monkeypatch.setattr(collector, "collect_geumsan_experience", fake_collect)
    target = router.CrawlTarget(
        provider=collector.GEUMSAN_EXPERIENCE_PROVIDER,
        name="금산군 청소년수련관 체험",
        branch=collector.GEUMSAN_EXPERIENCE_BRANCH,
        url=collector.GEUMSAN_EXPERIENCE_URL,
        source="test",
        priority=1,
        region=collector.GEUMSAN_EXPERIENCE_MUNICIPALITY_NAME,
        extra={},
    )
    rows, parser, meta = router.collect_from_url(
        target, timeout=3, max_depth=0, max_pages=20, detail_limit=5
    )
    assert rows and parser == "fixture" and meta == {"ok": True}
    assert len(calls) == 1


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
        if item.get("provider") == collector.GEUMSAN_EXPERIENCE_PROVIDER
    ]
    assert len(matches) == 1
    assert matches[0]["candidate_id"] == collector.GEUMSAN_EXPERIENCE_CANDIDATE_ID
    assert matches[0]["url"] == collector.GEUMSAN_EXPERIENCE_URL
    assert matches[0]["crawler_module"] == "Crawler.municipal_geumsan_experience"
    assert matches[0]["ops_scopes"] == ["experience"]
    assert matches[0]["service_group"] == "체험"
    assert matches[0]["service_group_policy"] == "locked"

    operational = yaml.safe_load(
        (root / "config/municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )
    matches = [
        item
        for item in operational["entries"]
        if item.get("provider") == collector.GEUMSAN_EXPERIENCE_PROVIDER
    ]
    assert len(matches) == 1
    assert matches[0]["normalized_url"] == collector.GEUMSAN_EXPERIENCE_URL
    assert matches[0]["row_count"] == 5
    assert matches[0]["source_total"] == 185
    assert matches[0]["current_source_count"] == 23


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_GEUMSAN_EXPERIENCE") != "1",
    reason="set RUN_LIVE_GEUMSAN_EXPERIENCE=1 for the official safe-GET contract",
)
def test_live_geumsan_185_23_5_contract() -> None:
    rows, parser, meta = collector.collect_geumsan_experience(
        _target(),
        today="2026-08-05",
        timeout=30,
        max_pages=20,
        detail_limit=5,
    )
    assert parser == collector.GEUMSAN_EXPERIENCE_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_total"] == 185
    assert meta["current_future_count"] == 23
    assert meta["returned_count"] == len(rows) == 5
    assert meta["post_last_clamp_page"] == 20
    assert meta["clamp_identity_sequence_verified"] is True
    assert meta["clamp_rowset_verified"] is True
    assert meta["application_endpoint_requests"] == 0
    assert all(row["application_url"] == "" for row in rows)
    assert all(row["service_group"] == "체험" for row in rows)
