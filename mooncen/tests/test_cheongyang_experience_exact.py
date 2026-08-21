from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from Crawler import Crawler_MunicipalYaml as router
from Crawler import municipal_cheongyang_experience as collector


PROGRAMS = (
    {
        "identity": "42",
        "title": "어린이 역사 공방 '백제 와박사의 하루' (대기 신청)",
        "status": "접수중",
        "start": "2026-08-06",
        "end": "2026-08-06",
        "apply_start": "2026-07-28",
        "apply_end": "2026-08-06",
        "target": "초등학생",
        "fee": "10,000원",
        "current": 2,
        "capacity": 30,
        "virtual": True,
    },
    {
        "identity": "29",
        "title": "어린이 역사 공방 '백제 와박사의 하루' (8월 8일 14시 ~ 16시)",
        "status": "접수중",
        "start": "2026-08-08",
        "end": "2026-08-08",
        "apply_start": "2026-07-01",
        "apply_end": "2026-08-07",
        "target": "초등학생",
        "fee": "10,000원",
        "current": 12,
        "capacity": 20,
        "virtual": False,
    },
    {
        "identity": "28",
        "title": "어린이 역사 공방 '백제 와박사의 하루' (8월 8일 10시 ~ 12시)",
        "status": "접수중",
        "start": "2026-08-08",
        "end": "2026-08-08",
        "apply_start": "2026-07-01",
        "apply_end": "2026-08-07",
        "target": "초등학생",
        "fee": "10,000원",
        "current": 13,
        "capacity": 20,
        "virtual": False,
    },
    *(
        {
            "identity": identity,
            "title": f"지난 백제 문화 체험 {identity}",
            "status": "접수종료",
            "start": "2023-10-19",
            "end": "2023-10-22",
            "apply_start": "2023-09-01",
            "apply_end": "2023-10-18",
            "target": "모든 대상",
            "fee": "4,000원",
            "current": 1000,
            "capacity": 1000,
            "virtual": False,
        }
        for identity in ("27", "26", "22", "19", "17")
    ),
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
        "provider": collector.CHEONGYANG_EXPERIENCE_PROVIDER,
        "url": collector.CHEONGYANG_EXPERIENCE_URL,
    }


def _title() -> str:
    return "교육/체험 예약 &gt; 교육/체험 &gt;"


def _list_html(page: int, *, sentinel_nonempty: bool = False) -> bytes:
    selected = PROGRAMS[(page - 1) * 5 : page * 5]
    if page == 3 and sentinel_nonempty:
        selected = PROGRAMS[:1]
    cards = []
    for item in selected:
        href = (
            "/prog/experCate/museum/sub04_02/view.do?"
            f"exper_no={item['identity']}&amp;pageIndex=1"
        )
        cards.append(
            f"""
            <li class="open"><figure><div class="ex_info"><figcaption>
              <b class="p_tit"><span class="cat">{item['status']}</span>
                <a href="{href}">{item['title']}</a></b>
              </figcaption><ul class="info">
                <li><b>운영기간</b>{item['start']}~{item['end']}</li>
                <li><b>대상</b>{item['target']}</li>
                <li><b>체험비</b>{item['fee']}</li>
                <li><b>정원</b>{item['capacity']}명</li>
              </ul></div>
              <div class="btn_wrap"><a class="bn bn_view" href="{href}">자세히보기</a></div>
            </figure></li>
            """
        )
    return f"""
    <!doctype html><html><head><title>{_title()}</title></head><body>
      <div id="txt"><form method="post"
        action="/prog/experCate/museum/sub04_02/list.do">
        <input name="pageIndex" type="hidden" value="{page}">
        <select name="searchCondition"><option value="subject">체험명</option>
          <option value="descript">체험내용</option></select>
        <span class="count_num">- 총 <span class="red">8</span>건 등록되어 있습니다.</span>
      </form><div class="res_lst special bigcon"><ul class="sdisplay_list">
        {''.join(cards)}
      </ul></div></div>
    </body></html>
    """.encode()


def _detail_html(
    identity: str,
    *,
    bad_application_path: bool = False,
    bad_applicant_href: str = "",
) -> bytes:
    item = next(program for program in PROGRAMS if program["identity"] == identity)
    content = (
        "체험 활동 : 워터클레이 화병 만들기. 직접 만들어 봅니다. "
        + (
            "특정 회차 및 날짜 지정이 불가능합니다. "
            "시스템상 표시되는 운영 기간 및 시간은 대기 접수를 위해 "
            "임의로 설정된 가상의 일정입니다."
            if item["virtual"]
            else "실제 고정 회차에 진행합니다."
        )
    )
    application_path = (
        "/prog/experCate/museum/sub04_02/login.do"
        if bad_application_path
        else "/prog/experReservation/museum/sub04_02/write.do"
    )
    return f"""
    <!doctype html><html><head><title>{_title()}</title></head><body>
      <div id="txt"><div class="res_lst special bigcon detail">
        <ul class="sdisplay_list"><li class="open"><figure><div class="ex_info">
          <figcaption><b class="p_tit"><span class="cat">{item['status']}</span>
            <a name="subject">{item['title']}</a></b></figcaption>
          <ul class="info">
            <li><b>운영기간</b>{item['start']}~{item['end']}</li>
            <li><b>신청기간</b>{item['apply_start']}~{item['apply_end']}</li>
            <li><b>대상</b>{item['target']}</li>
            <li><b>체험비</b>{item['fee']}</li>
            <li><b>신청/정원</b>{item['current']} / {item['capacity']}명</li>
            <li><b>운영장소</b>백제문화체험박물관 교육체험실</li>
            <li><b>문의</b>041-940-4871</li>
          </ul><div class="btn_wrap"><a class="bn bn_list"
            href="/prog/experCate/museum/sub04_02/list.do?exper_no={identity}&amp;pageIndex=1">목록보기</a></div>
        </div></figure></li></ul>
      </div>
      <h3>체험내용</h3><p class="caption_detail">{content}</p>
      <table class="schcal_tbl"><tr><td><a class="ov"
        href="{application_path}?exper_no={identity}&amp;exper_date={item['start']}">8 신청가능</a></td></tr></table>
      <a class="bn bn_list" href="{bad_applicant_href or f'/prog/experReservation/museum/sub04_02/list.do?exper_no={identity}&amp;pageIndex=1'}">신청자보기</a>
      </div>
    </body></html>
    """.encode()


def _collect(
    *,
    detail_limit: int = 10,
    sentinel_nonempty: bool = False,
    bad_application_path: bool = False,
    bad_applicant_href: str = "",
):
    calls: list[str] = []
    session = _Session()

    def fetcher(_session: Any, url: str, _timeout: int) -> _Response:
        calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == collector.CHEONGYANG_EXPERIENCE_LIST_PATH:
            return _Response(
                url,
                _list_html(
                    int(query["pageIndex"][0]),
                    sentinel_nonempty=sentinel_nonempty,
                ),
            )
        return _Response(
            url,
            _detail_html(
                query["exper_no"][0],
                bad_application_path=bad_application_path,
                bad_applicant_href=bad_applicant_href,
            ),
        )

    rows, parser, meta = collector.collect_cheongyang_experience(
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
    assert collector.is_cheongyang_experience_target(_target())
    assert not collector.is_cheongyang_experience_target(
        {**_target(), "url": collector.CHEONGYANG_EXPERIENCE_URL + "?pageIndex=1"}
    )
    assert collector._request_kind(collector.cheongyang_experience_list_url(2)) == "list"
    assert collector._request_kind(collector.cheongyang_experience_detail_url("29")) == "detail"
    for unsafe in (
        "https://www.cheongyang.go.kr/prog/experReservation/museum/sub04_02/write.do?exper_no=29&exper_date=2026-08-08",
        "https://www.cheongyang.go.kr/prog/experReservation/museum/sub04_02/list.do?exper_no=29&pageIndex=1",
        "https://www.cheongyang.go.kr/prog/experCate/museum/sub04_02/login.do",
        "https://www.cheongyang.go.kr/cmm/fms/FileDown.do?atchFileId=1",
        "https://www.cheongyang.go.kr/prog/experCate/museum/sub04_02/view.do?exper_no=29&rdate=2026-09-01",
    ):
        with pytest.raises(collector.CheongyangExperienceContractError):
            collector._request_kind(unsafe)


def test_complete_fixture_quarantines_virtual_waitlist_and_returns_two_rounds() -> None:
    rows, parser, meta, calls, session = _collect()

    assert parser == collector.CHEONGYANG_EXPERIENCE_PARSER
    assert [row["raw_fields"]["identity"] for row in rows] == ["29", "28"]
    assert [row["schedule_raw"] for row in rows] == ["14:00 ~ 16:00", "10:00 ~ 12:00"]
    assert all(row["status"] == "OPEN" for row in rows)
    assert all(row["reservation_available"] is True for row in rows)
    assert all(row["application_url"] == "" for row in rows)
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["venue_name"] == "백제문화체험박물관 교육체험실" for row in rows)
    assert meta["source_total"] == 8
    assert meta["page_counts"] == {1: 5, 2: 3}
    assert meta["sentinel_page"] == 3 and meta["sentinel_count"] == 0
    assert meta["current_source_count"] == meta["detail_verified"] == 3
    assert meta["expired_count"] == 5
    assert meta["excluded_count"] == 1
    assert meta["excluded_reason_counts"] == {"virtual_waitlist_schedule": 1}
    assert meta["excluded_rows"] == [
        {
            "identity": "42",
            "reason": "virtual_waitlist_schedule",
            "source_status": "접수중",
        }
    ]
    assert meta["status_counts"] == {"OPEN": 2}
    assert meta["application_control_count"] == 3
    assert meta["applicant_control_count"] == 3
    assert meta["application_url_persisted_count"] == 0
    assert meta["reservation_available_count"] == 2
    assert meta["list_requests"] == 6 and meta["detail_requests"] == 3
    assert meta["logical_requests"] == 9
    assert meta["snapshot_complete"] is meta["details_complete"] is True
    assert session.closed is True
    assert not any(
        marker in url.lower()
        for url in calls
        for marker in (
            "experreservation",
            "login",
            "member",
            "applicant",
            "filedown",
            "download",
        )
    )
    for key in (
        "application_endpoint_requests",
        "applicant_endpoint_requests",
        "login_endpoint_requests",
        "member_endpoint_requests",
        "identity_endpoint_requests",
        "file_endpoint_requests",
        "attachment_endpoint_requests",
        "download_endpoint_requests",
        "pii_endpoint_requests",
    ):
        assert meta[key] == 0


def test_contract_drift_and_detail_cap_are_atomic() -> None:
    rows, _, meta, _, session = _collect(sentinel_nonempty=True)
    assert rows == [] and meta["snapshot_complete"] is False
    assert "declared total differs" in meta["errors"][0]
    assert session.closed is True

    rows, _, meta, _, _ = _collect(bad_application_path=True)
    assert rows == [] and "application identity/path changed" in meta["errors"][0]

    rows, _, meta, calls, _ = _collect(detail_limit=2)
    assert rows == [] and "detail_limit" in meta["errors"][0]
    assert not any(collector.CHEONGYANG_EXPERIENCE_DETAIL_PATH in url for url in calls)


@pytest.mark.parametrize(
    "bad_applicant_href",
    (
        "https://evil.example/prog/experReservation/museum/sub04_02/list.do?exper_no=42&pageIndex=1",
        "/prog/experReservation/museum/sub04_02/list.do?exper_no=42&pageIndex=1&unexpected=1",
    ),
)
def test_applicant_control_requires_exact_origin_and_query(
    bad_applicant_href: str,
) -> None:
    rows, _, meta, calls, _ = _collect(bad_applicant_href=bad_applicant_href)

    assert rows == []
    assert "applicant control changed" in meta["errors"][0]
    assert not any("experReservation" in url for url in calls)


def test_router_dispatches_exact_target_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    def fake_collect(target: Any, **kwargs: Any):
        calls.append((target, kwargs))
        return [
            {"provider": collector.CHEONGYANG_EXPERIENCE_PROVIDER}
        ], "fixture", {"ok": True}

    monkeypatch.setattr(collector, "collect_cheongyang_experience", fake_collect)
    target = router.CrawlTarget(
        provider=collector.CHEONGYANG_EXPERIENCE_PROVIDER,
        name="청양 백제문화체험박물관 체험",
        branch="백제문화체험박물관",
        url=collector.CHEONGYANG_EXPERIENCE_URL,
        source="test",
        priority=1,
        region="충청남도 청양군",
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
        if item.get("provider") == collector.CHEONGYANG_EXPERIENCE_PROVIDER
    ]
    assert len(matches) == 1
    assert matches[0]["url"] == collector.CHEONGYANG_EXPERIENCE_URL
    assert matches[0]["crawler_module"] == "Crawler.municipal_cheongyang_experience"
    assert matches[0]["ops_scopes"] == ["experience"]

    operational = yaml.safe_load(
        (root / "config/municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert any(
        item.get("provider") == collector.CHEONGYANG_EXPERIENCE_PROVIDER
        and item.get("row_count") == 2
        for item in operational["entries"]
    )
