from __future__ import annotations

import hashlib
import os
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_taebaek as taebaek


class FakeSession:
    def close(self) -> None:
        pass


def target(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "provider": taebaek.TAEBAEK_PROVIDER,
        "url": taebaek.TAEBAEK_CANONICAL_URL,
        "source_group": "municipal_reservation",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
    }
    value.update(overrides)
    return value


def _course_row(*, current: bool, application: bool = True) -> str:
    if current:
        apply_period = (
            "1차접수 : 26.07.01 ~ 26.07.31 "
            "2차접수 : 26.08.01 ~ 26.08.02 "
            "3차접수 : 26.08.03 ~ 26.08.04"
        )
        event_period = "교육 : 26.07.20 ~ 26.08.20"
        status = '<a href="./do-not-fetch.do">접수중 신청</a>' if application else "접수중"
    else:
        apply_period = "1차접수 : 26.03.30 ~ 26.03.31"
        event_period = "교육 : 26.04.01 ~ 26.07.03"
        status = "교육마감"
    return f"""
      <tr>
        <td>1</td><td>취미·소양</td>
        <td><a href="./webSelectLctreManageView.do?key=1632&amp;lctreSe=LCTRESE01&amp;lctreNo=1263&amp;pageUnit=10&amp;pageIndex=1&amp;searchCnd=all">안전한 교육강좌</a></td>
        <td>버리는 강사명</td>
        <td>{apply_period}<br>{event_period}</td>
        <td>성인</td><td>정원 : 3/10<br>대기 : 0/100</td><td>{status}</td>
      </tr>
    """


def _list_html(
    partition: taebaek.TaebaekPartition,
    page: int,
    *,
    total: int,
    row: str = "",
) -> str:
    last = max(1, (total + 9) // 10)
    return f"""
      <html><head><title>{partition.name} 목록 - 강좌신청 - 프로그램 - 평생학습관</title></head>
      <body>
        <form name="lctreManageVOForm" action="./webSelectLctreManageList.do">
          <input type="hidden" name="key" value="{partition.key}">
          <input type="hidden" name="lctreSe" value="{partition.course_type}">
          <input type="text" name="lctreBgnde" value="2026-01-01">
        </form>
        <div class="row"><div class="col-sm-24 small">총 {total}건 [ {page}/{last} 페이지 ]</div></div>
        <table>
          <caption>{taebaek._LIST_CAPTION}</caption>
          <tbody>{row}</tbody>
        </table>
      </body></html>
    """


def _detail_html(*, application: bool = True) -> str:
    fields = {
        "강좌구분": "정규강좌",
        "분류": "취미·소양",
        "개요": "저장하지 않는 공개 상세 설명",
        "강사명": "버리는 강사명",
        "경력": "버리는 경력",
        "계획서": "",
        "기관": "태백시",
        "장소": "평생학습관 202호",
        "접수기간": "2026-07-01 09:00 ~ 2026-07-31 18:00",
        "교육기간": "2026-07-20 ~ 2026-08-20",
        "교육시간": "18:30 ~ 20:30",
        "교육요일": "월,수",
        "모집인원": "정원 : 3/10 명 대기 : 0/100 명",
        "문의전화": "0335500000",
        "접수방법": "온라인접수",
        "교육대상": "성인",
    }
    rows = "".join(f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in fields.items())
    control = '<a class="p-button">신청하기</a>' if application else ""
    return f"""
      <html><head><title>정규강좌 상세 - 평생학습관</title></head><body>
        <div class="education_title"><h3 class="h0">
          <span class="education_state">교육중</span>안전한 교육강좌
        </h3></div>
        <table><caption>{taebaek._DETAIL_CAPTION}</caption><tbody>{rows}</tbody></table>
        <div class="boardBtn">{control}<a class="p-button">목록</a></div>
      </body></html>
    """


def fake_source(*, current: bool, bad_sentinel: bool = False):
    calls: list[str] = []

    def fetcher(session: object, url: str, timeout: int) -> str:
        del session, timeout
        calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == taebaek.TAEBAEK_DETAIL_PATH:
            return _detail_html(application=current)
        partition = taebaek.TAEBAEK_PARTITION_BY_PAIR[
            (query["key"][0], query["lctreSe"][0])
        ]
        page = int(query["pageIndex"][0])
        total = 1 if partition.code == "regular" else 0
        row = _course_row(current=current) if total and page == 1 else ""
        if bad_sentinel and partition.code == "online" and page == 2:
            row = _course_row(current=False)
        return _list_html(partition, page, total=total, row=row)

    return calls, fetcher


def test_identity_hashes_and_exact_target_contract() -> None:
    assert hashlib.sha1(taebaek.TAEBAEK_CANONICAL_URL.encode()).hexdigest().upper() == taebaek.TAEBAEK_URL_SHA1
    assert hashlib.sha256(taebaek.TAEBAEK_CANONICAL_URL.encode()).hexdigest().upper() == taebaek.TAEBAEK_URL_SHA256
    assert taebaek.TAEBAEK_CANONICAL_CANDIDATE_ID == "MUNI_IR_" + taebaek.TAEBAEK_URL_SHA256[:12]
    assert taebaek.is_target(target())
    assert not taebaek.is_target(target(provider="MUNI_WRONG"))
    assert not taebaek.is_target(target(url=taebaek.TAEBAEK_CANONICAL_URL + "&pageIndex=1"))
    assert len(taebaek.TAEBAEK_PARTITIONS) == 5


def test_complete_five_partition_snapshot_and_current_detail() -> None:
    calls, fetcher = fake_source(current=True)
    rows, parser, meta = taebaek.collect(
        target(),
        today="2026-07-23",
        session_factory=FakeSession,
        fetcher=fetcher,
    )

    assert parser == taebaek.TAEBAEK_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["route_totals"] == {
        "regular": 1,
        "online": 0,
        "academy": 0,
        "special": 0,
        "custom": 0,
    }
    assert meta["source_total"] == 1
    assert meta["current_source_count"] == 1
    assert meta["list_requests"] == 20
    assert meta["detail_pages"] == 1
    assert meta["logical_requests"] == 21
    assert meta["snapshot_complete"] is True
    assert len(rows) == 1
    row = rows[0]
    assert row["provider_course_id"] == f"{taebaek.TAEBAEK_PROVIDER}:lecture:1263"
    assert row["branch"] == "태백시 평생학습관"
    assert row["address"] == taebaek.TAEBAEK_BRANCH_ADDRESS
    assert row["status"] == "OPEN"
    assert row["capacity_total"] == 10
    assert row["apply_period"] == "2026-07-01 ~ 2026-08-04"
    assert row["reservation_available"] is True
    assert row["application_url"] == row["raw_url"]
    assert row["raw_fields"]["application_endpoint_fetched"] is False
    assert not any("do-not-fetch.do" in url for url in calls)
    assert not any("downloadAtchFile" in url for url in calls)
    assert all("0335500000" not in str(value) for value in row.values())
    assert all("버리는 강사명" not in str(value) for value in row.values())
    assert all("저장하지 않는 공개 상세 설명" not in str(value) for value in row.values())


def test_expired_complete_snapshot_is_verified_no_current_data() -> None:
    calls, fetcher = fake_source(current=False)
    rows, _, meta = taebaek.collect(
        target(),
        today="2026-07-23",
        session_factory=FakeSession,
        fetcher=fetcher,
    )

    assert rows == []
    assert meta["source_total"] == 1
    assert meta["current_source_count"] == 0
    assert meta["expired_source_count"] == 1
    assert meta["detail_pages"] == 0
    assert meta["list_requests"] == 20
    assert meta["no_current_data"] is True
    assert meta["snapshot_complete"] is True
    assert not any(taebaek.TAEBAEK_DETAIL_PATH in url for url in calls)


def test_nonempty_post_last_page_fails_closed() -> None:
    _, fetcher = fake_source(current=False, bad_sentinel=True)
    rows, _, meta = taebaek.collect(
        target(),
        today="2026-07-23",
        session_factory=FakeSession,
        fetcher=fetcher,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sentinel" in meta["configured_collection_error"]


def test_caps_and_external_dedupe_fail_closed() -> None:
    _, fetcher = fake_source(current=True)
    rows, _, meta = taebaek.collect(
        target(),
        today="2026-07-23",
        detail_limit=0,
        session_factory=FakeSession,
        fetcher=fetcher,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit" in meta["configured_collection_error"]

    _, fetcher = fake_source(current=True)
    rows, _, meta = taebaek.collect(
        target(),
        today="2026-07-23",
        session_factory=FakeSession,
        fetcher=fetcher,
        dedupe_rows=lambda rows: [],
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_TAEBAEK_TESTS") != "1",
    reason="set RUN_LIVE_TAEBAEK_TESTS=1 for two consecutive official-source snapshots",
)
def test_live_taebaek_snapshot_is_stable_twice() -> None:
    snapshots = []
    for _ in range(2):
        rows, _, meta = taebaek.collect(target(), today="2026-08-05", timeout=15)
        assert meta["configured_collection_error"] == ""
        assert meta["snapshot_complete"] is True
        assert meta["route_totals"] == taebaek.TAEBAEK_LIVE_AUDIT_BASELINE["route_totals"]
        assert meta["route_pages"] == taebaek.TAEBAEK_LIVE_AUDIT_BASELINE["route_pages"]
        assert meta["source_total"] == 38
        assert meta["current_source_count"] == 38
        assert meta["detail_pages"] == 38
        assert meta["list_requests"] == 24
        assert meta["application_endpoints_called"] == 0
        assert len(rows) == 38
        snapshots.append((rows, meta["source_status_counts"]))
    assert snapshots[0] == snapshots[1]
