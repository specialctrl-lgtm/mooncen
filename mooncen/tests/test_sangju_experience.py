from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
import hashlib
import math
import os
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from Crawler import Crawler_MunicipalYaml as municipal
from Crawler import municipal_sangju as education
from Crawler import municipal_sangju_experience as sangju


class _Response:
    def __init__(self, url: str, html: str):
        self.url = url
        self.content = html.encode("utf-8")
        self.status_code = 200
        self.history: list[object] = []


class _Session:
    def __init__(self, fixture: "_Fixture") -> None:
        self.fixture = fixture
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target() -> dict[str, str]:
    return {
        "provider": sangju.SANGJU_EXPERIENCE_PROVIDER,
        "url": sangju.SANGJU_EXPERIENCE_CANONICAL_URL,
    }


@dataclass
class _DispatchTarget:
    provider: str = sangju.SANGJU_EXPERIENCE_PROVIDER
    url: str = sangju.SANGJU_EXPERIENCE_CANONICAL_URL
    name: str = "상주시 통합예약 전체 체험·견학"
    branch: str = "경상북도 상주시"


def _reception(
    status: str,
    identity: str,
    *,
    apply_period: str = ("2024년 01월 01일 09시 00분 ~ 2024년 01월 02일 18시 00분"),
) -> dict[str, str]:
    return {
        "status": status,
        "heading": "온라인 접수",
        "apply_period": apply_period,
        "rcpt_no": str(int(identity) + 6) if status == "예약" else "",
    }


def _row(
    identity: str,
    title: str,
    facility_code: str,
    period: str,
    address: str,
    status: str,
) -> dict[str, object]:
    facility = sangju.SANGJU_EXPERIENCE_FACILITY_BY_CODE[facility_code]
    if status == "정보제공":
        receptions: list[dict[str, str]] = []
    elif status == "예약불가":
        receptions = [{"status": "예약불가"}]
    else:
        receptions = [_reception(status, identity)]
    return {
        "identity": identity,
        "title": title,
        "facility_code": facility_code,
        "facility": facility.name,
        "period": period,
        "address": address,
        "status": status,
        "receptions": receptions,
    }


_ROWS = [
    {
        **_row(
            "100609",
            "상주시립도서관 견학 프로그램 「도서관에서 놀자!」",
            "129",
            "2026-04-01 ~ 2026-08-26",
            "경북 상주시 복룡2길 22 (복룡동) 상주시립도서관",
            "예약",
        ),
        "receptions": [
            _reception(
                "예약",
                "100609",
                apply_period=("2026년 03월 25일 10시 00분 ~ 2026년 08월 25일 23시 59분"),
            )
        ],
        "detail_period": ("2026년 04월 01일 10시 00분 ~ 2026년 08월 26일 11시 00분"),
    },
    _row(
        "100508",
        "(숙박형)다목적 패키지",
        "135",
        "2025-06-30 ~ 상시운영",
        "경북 상주시 낙동면 낙동1길 144-10 (낙동리, 상주시 청소년 해양교육원) 일원",
        "예약",
    ),
    _row(
        "100313",
        "2025년 낙동강 어린이 수상안전교육장",
        "100004",
        "2025-07-16 ~ 2025-09-30",
        "경북 상주시 도남동 810-1 상주보오토캠핑장 내",
        "예약",
    ),
    _row(
        "100110",
        "상주시국제승마장관리사업소 승마강습",
        "120",
        "2025-06-06 ~ 상시운영",
        "경북 상주시 사벌국면 국제승마장로 1 (화달리, 상주시국제승마장) 상주시국제승마장관리사업소",
        "정보제공",
    ),
    _row(
        "475",
        "2024년 상주박물관 놀이반장 6회",
        "113",
        "2024-11-09 ~ 2024-11-09",
        "경북 상주시 사벌국면 경천로 684 (삼덕리, 상주박물관)",
        "예약불가",
    ),
    _row(
        "453",
        "2024년 상주박물관 놀이반장 5회",
        "113",
        "2024-10-12 ~ 2024-10-12",
        "경북 상주시 사벌국면 경천로 684 (삼덕리, 상주박물관)",
        "종료",
    ),
    _row(
        "444",
        "2024년 상주박물관 놀이반장 4회",
        "113",
        "2024-09-28 ~ 2024-09-28",
        "경북 상주시 사벌국면 경천로 684 (삼덕리, 상주박물관)",
        "종료",
    ),
    _row(
        "334",
        "2024년 상주박물관 놀이반장 3회",
        "113",
        "2024-07-13 ~ 2024-07-13",
        "경북 상주시 사벌국면 경천로 684 (삼덕리, 상주박물관)",
        "종료",
    ),
    _row(
        "263",
        "2024년 상주박물관 놀이반장 2회",
        "113",
        "2024-06-08 ~ 2024-06-02",
        "경북 상주시 사벌국면 경천로 684 (삼덕리, 상주박물관)",
        "종료",
    ),
    _row(
        "254",
        "2024년 상주박물관 놀이반장 1회",
        "113",
        "2024-05-25 ~ 2024-05-25",
        "경북 상주시 사벌국면 경천로 684 (삼덕리, 상주박물관)",
        "종료",
    ),
    _row(
        "205",
        "거꾸로 옛이야기나라숲 이야기공작소",
        "111",
        "2024-03-04 ~ 상시운영",
        "경북 상주시 화북면 우복동길 63-23 (용유리)",
        "정보제공",
    ),
    _row(
        "204",
        "상주시농기계임대사업",
        "119",
        "2024-03-04 ~ 상시운영",
        "경북 상주시 발산로 71 (초산동, 상주시농업기술센터)",
        "정보제공",
    ),
    _row(
        "203", "상주목재문화체험장", "89", "2024-03-04 ~ 상시운영", "경북 상주시 은척면 성주봉로 3 (남곡리)", "정보제공"
    ),
    _row("202", "상주시힐링센터", "90", "2024-03-04 ~ 상시운영", "경북 상주시 은척면 성주봉로 3 (남곡리)", "정보제공"),
    _row(
        "198",
        "상주시육아종합지원센터 싱글벙글 놀이실",
        "92",
        "2024-03-04 ~ 상시운영",
        "경북 상주시 무양2길 49 (무양동)",
        "정보제공",
    ),
    _row("170", "상주보 물놀이장", "105", "2024-07-01 ~ 2024-09-30", "경북 상주시 도남동 146", "예약불가"),
    _row(
        "165",
        "밀리터리 테마파크 일반 예약",
        "102",
        "2024-03-04 ~ 상시운영",
        "경북 상주시 사벌국면 경천로 654 (삼덕리)",
        "예약불가",
    ),
]


def _shell(body: str) -> str:
    return (
        "<html><head><title>통합예약 홈페이지 &gt; 체험·견학 &gt; "
        "온라인예약 | 상주시</title></head><body>"
        f"{body}<footer>(37211) 경상북도 상주시 상산로 223(남성동 140-3)"
        "</footer></body></html>"
    )


def _facility_tabs(selected_code: str, *, registry_drift: bool = False) -> str:
    entries = [
        ("", "전체"),
        *((item.code, item.name) for item in sangju.SANGJU_EXPERIENCE_FACILITIES),
    ]
    if registry_drift:
        entries.pop()
    buttons = []
    for code, name in entries:
        active = ' class="active"' if code == selected_code else ""
        buttons.append(
            f'<li{active}><button type="button" onclick="reserveList.searchFacility(\'{code}\');">{name}</button></li>'
        )
    return '<ul class="com_tab com_tab2">' + "".join(buttons) + "</ul>"


def _reception_html(item: dict[str, str], namespace: str) -> str:
    if item["status"] == "예약불가":
        return (
            '<li><ul class="tm_cir"><li><span class="color_red">'
            "[예약불가]</span>등록된 접수 정보가 없습니다.</li></ul></li>"
        )
    status = item["status"]
    if status == "예약":
        control = f'<a href="javascript:;" onclick="{namespace}.apply(\'{item["rcpt_no"]}\');">예약</a>'
    else:
        control = '<a class="bg_dark" href="javascript:;" onclick="alert(\'접수가 종료되었습니다.\')">종료</a>'
    return (
        f'<li><h2>{item["heading"]}</h2><ul class="tm_cir">'
        f"<li><span>접수기간</span>{item['apply_period']}</li>"
        f"</ul>{control}</li>"
    )


def _badge(row: dict[str, object]) -> str:
    if row["status"] == "예약" or row["identity"] in {"170", "165"}:
        return "<span>온라인예약 접수중</span>"
    if row["status"] == "정보제공":
        return '<span class="bg_sky2">정보제공</span>'
    return ""


def _card(row: dict[str, object], ordinal: int) -> str:
    identity = str(row["identity"])
    receptions = list(row["receptions"])
    reception_area = ""
    if receptions:
        reception_area = (
            '<ul class="list_sub mb">' + "".join(_reception_html(item, "reserveList") for item in receptions) + "</ul>"
        )
    return f"""
      <section><div class="flex"><div class="right">
        <div class="top">{_badge(row)}</div>
        <h1><em>{ordinal:02d}</em><a href="#none" onclick="reserveList.detail('{identity}');">{row["title"]}</a></h1>
        <ul class="tm_cir">
          <li><span>분류</span>체험/견학</li>
          <li><span>시설명</span>{row["facility"]}</li>
          <li><span>운영기간</span>{row["period"]}</li>
          <li><span>주소</span>{row["address"]}</li>
        </ul>
        <a href="javascript:;" onclick="reserveList.detail('{identity}')">상세보기</a>
      </div></div>{reception_area}</section>
    """


def _list_html(
    rows: list[dict[str, object]],
    page: int,
    facility_code: str,
    *,
    registry_drift: bool = False,
    sentinel_active: bool = False,
) -> str:
    total = len(rows)
    last = max(1, math.ceil(total / sangju.SANGJU_EXPERIENCE_PAGE_SIZE))
    start = (page - 1) * sangju.SANGJU_EXPERIENCE_PAGE_SIZE
    selected = rows[start : start + sangju.SANGJU_EXPERIENCE_PAGE_SIZE]
    ledger = "".join(_card(row, total - start - position) for position, row in enumerate(selected))
    if not ledger:
        ledger = '<section><p class="no_data">자료가 없습니다.</p></section>'
    active = f'<a class="active">{page}</a>' if page <= last or sentinel_active else ""
    return _shell(
        f"""
        <form id="reserveListForm" name="reserveListForm">
          <input type="hidden" name="pageNo" value="11881">
          <input type="hidden" name="mn" value="15383">
          <input type="hidden" name="pageIndex" value="{page}">
          <input type="hidden" name="searchTrgtClsfCd" value="RMS004004">
          <input type="hidden" name="searchFcltNo" value="{facility_code}">
          <input type="hidden" name="cyclNo" value="">
          <input type="hidden" name="rcptNo" value="">
          {_facility_tabs(facility_code, registry_drift=registry_drift)}
          <div class="list3" id="reserveList">{ledger}</div>
          <ul class="pager">{active}<a class="pager_arrow pager_next_all"
            href="?pageIndex={last}" onclick="reserveList.pageMove({last}); return false;">&gt;&gt;</a></ul>
        </form>
        """
    )


def _detail_html(row: dict[str, object], *, title_drift: bool = False) -> str:
    identity = str(row["identity"])
    title = str(row["title"]) + (" 변경" if title_drift else "")
    receptions = list(row["receptions"])
    motion = (
        '<div class="motion_wrap"><ul class="list_sub">'
        + "".join(_reception_html(item, "reserveDetail") for item in receptions)
        + "</ul></div>"
    )
    return _shell(
        f"""
        <form id="reserveDetailForm" name="reserveDetailForm">
          <input type="hidden" name="pageNo" value="11881">
          <input type="hidden" name="mn" value="15383">
          <input type="hidden" name="pageIndex" value="">
          <input type="hidden" name="searchTrgtClsfCd" value="RMS004004">
          <input type="hidden" name="searchFcltNo" value="">
          <input type="hidden" name="cyclNo" value="{identity}">
          <input type="hidden" name="rcptNo" value="">
          <div class="img_jb"><div class="right">
            <div class="top"><span>온라인예약 접수중</span></div><h1>{title}</h1>
            <ul class="tm_cir">
              <li><span>분류</span>체험/견학</li>
              <li><span>시설명</span>{row["facility"]}</li>
              <li><span>주소<button type="button">주소복사</button></span>{row["address"]}</li>
              <li><span>운영기간</span>{row["detail_period"]}</li>
              <li><span>강사</span>저장 금지 이름</li>
            </ul>
          </div></div>
          {motion}
          <div class="tabpanel_wrap">
            <div class="bd_scroll">문의 054-123-4567 private@example.com</div>
            <div class="bd_scroll"><a href="/file/readFile.tc?fileId=PRIVATE">첨부</a></div>
            <div class="bd_scroll">저장 금지 자유서술</div>
          </div>
          <img src="/file/readFile.tc?scale=PRIVATE">
          <div class="bot_btn"><a href="javascript:;" onclick="reserveDetail.list();">목록으로</a></div>
        </form>
        """
    )


class _Fixture:
    def __init__(
        self,
        *,
        partition_drop: bool = False,
        registry_drift: bool = False,
        boundary_drift: bool = False,
        sentinel_drift: bool = False,
        detail_drift: bool = False,
        anomaly_drift: bool = False,
    ) -> None:
        self.partition_drop = partition_drop
        self.registry_drift = registry_drift
        self.boundary_drift = boundary_drift
        self.sentinel_drift = sentinel_drift
        self.detail_drift = detail_drift
        self.anomaly_drift = anomaly_drift
        self.requests: list[str] = []
        self.sessions: list[_Session] = []
        self.counts: Counter[tuple[str, int]] = Counter()
        self.lock = Lock()

    def session_factory(self) -> _Session:
        session = _Session(self)
        self.sessions.append(session)
        return session

    def fetch(self, _session: _Session, url: str, _timeout: int) -> _Response:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        with self.lock:
            self.requests.append(url)
        if parsed.path == sangju.SANGJU_EXPERIENCE_LIST_PATH:
            page = int(query["pageIndex"][0])
            code = query["searchFcltNo"][0]
            self.counts[(code, page)] += 1
            rows = [dict(row) for row in _ROWS if not code or row["facility_code"] == code]
            if self.partition_drop and code == "113":
                rows.pop()
            if self.anomaly_drift:
                for row in rows:
                    if row["identity"] == "170":
                        row["title"] = "상주보 물놀이장 변경"
            if self.boundary_drift and not code and page == 1 and self.counts[(code, page)] > 1:
                rows[0]["title"] = str(rows[0]["title"]) + " 변경"
            return _Response(
                url,
                _list_html(
                    rows,
                    page,
                    code,
                    registry_drift=self.registry_drift,
                    sentinel_active=self.sentinel_drift and not code and page == 4,
                ),
            )
        if parsed.path == sangju.SANGJU_EXPERIENCE_DETAIL_PATH:
            identity = query["cyclNo"][0]
            row = next(item for item in _ROWS if item["identity"] == identity)
            return _Response(url, _detail_html(row, title_drift=self.detail_drift))
        raise AssertionError(f"forbidden endpoint requested: {url}")


def _collect(fixture: _Fixture, **overrides: object):
    options: dict[str, object] = {
        "today": "2026-08-05",
        "max_pages": 10,
        "detail_limit": 10,
        "session_factory": fixture.session_factory,
        "fetcher": fixture.fetch,
    }
    options.update(overrides)
    return sangju.collect_sangju_experience(_target(), **options)


def test_exact_target_owner_hashes_and_education_boundary() -> None:
    assert (
        hashlib.sha1(sangju.SANGJU_EXPERIENCE_CANONICAL_URL.encode()).hexdigest()
        == sangju.SANGJU_EXPERIENCE_CANONICAL_URL_SHA1
    )
    assert (
        hashlib.sha256(sangju.SANGJU_EXPERIENCE_CANONICAL_URL.encode()).hexdigest()
        == sangju.SANGJU_EXPERIENCE_CANONICAL_URL_SHA256
    )
    assert sangju.is_target(_target())
    assert not sangju.is_target({**_target(), "url": education.SANGJU_CANONICAL_URL})
    assert not education.is_target(_target())
    assert not sangju.is_target({**_target(), "url": sangju.SANGJU_EXPERIENCE_CANONICAL_URL + "&x=1"})


def test_exact_snapshot_partitions_sentinel_detail_privacy_and_directories() -> None:
    fixture = _Fixture()
    rows, parser, meta = _collect(fixture)
    assert parser == sangju.SANGJU_EXPERIENCE_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert len(rows) == meta["row_count"] == meta["current_source_count"] == 1
    assert meta["source_total_count"] == 17
    assert meta["standing_source_count"] == 8
    assert meta["expired_source_count"] == 8
    assert meta["page_sizes"] == [8, 8, 1]
    assert meta["post_last_page"] == 4
    assert meta["facility_partition_counts"] == sangju.SANGJU_EXPERIENCE_LIVE_AUDIT_BASELINE["facility_counts"]
    assert meta["facility_partition_union_count"] == 17
    assert meta["facility_partition_overlap_count"] == 0
    assert meta["current_ids"] == ["100609"]
    assert meta["current_facility_counts"] == {"129": 1}
    assert meta["list_requests"] == 19
    assert meta["detail_requests"] == 1
    assert meta["source_requests"] == 20
    assert all(session.closed for session in fixture.sessions)
    assert not any(urlparse(url).path == sangju.SANGJU_EXPERIENCE_APPLICATION_PATH for url in fixture.requests)
    assert not any("readFile.tc" in url for url in fixture.requests)

    row = rows[0]
    assert row["provider"] == sangju.SANGJU_EXPERIENCE_PROVIDER
    assert row["provider_course_id"].endswith(":cycl:100609")
    assert row["title"] == "상주시립도서관 견학 프로그램 「도서관에서 놀자!」"
    assert row["period"] == "2026-04-01 ~ 2026-08-26"
    assert row["branch_code"] == "129"
    assert row["domain_category"] == "체험·견학"
    assert row["source_group"] == "public_reservation"
    assert row["service_group"] == "체험"
    assert row["status"] == "OPEN"
    assert row["reservation_available"] is True
    assert row["raw_fields"]["application_endpoint_fetched"] is False
    assert row["raw_fields"]["attachment_endpoint_fetched"] is False
    payload = repr(row)
    assert "054-123-4567" not in payload
    assert "private@example.com" not in payload
    assert "저장 금지 자유서술" not in payload
    assert "fileId=PRIVATE" not in payload


def test_only_two_exact_badge_unavailable_anomalies_and_reversed_263_are_allowed() -> None:
    rows, _, meta = _collect(_Fixture())
    assert len(rows) == 1
    assert meta["source_raw_status_counts"]["예약불가"] == 3
    assert sangju._parse_iso_period("2024-06-08 ~ 2024-06-02", "263") == (
        date(2024, 6, 8),
        date(2024, 6, 2),
    )
    with pytest.raises(sangju.SangjuExperienceContractError, match="reversed"):
        sangju._parse_iso_period("2024-06-08 ~ 2024-06-02", "999")

    rows, _, meta = _collect(_Fixture(anomaly_drift=True))
    assert rows == []
    assert "unavailable/badge disagreement" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        (_Fixture(partition_drop=True), "partition union incomplete"),
        (_Fixture(registry_drift=True), "facility registry"),
        (_Fixture(boundary_drift=True), "source boundaries changed"),
        (_Fixture(sentinel_drift=True), "post-last page"),
        (_Fixture(detail_drift=True), "list/detail structured data drift"),
    ],
)
def test_contract_drift_fails_atomically(fixture: _Fixture, message: str) -> None:
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert message in meta["configured_collection_error"]


def test_caps_session_requirement_and_dedupe_cardinality_fail_closed() -> None:
    rows, _, meta = sangju.collect_sangju_experience(_target(), today="2026-08-05")
    assert rows == []
    assert "managed session_factory" in meta["configured_collection_error"]

    rows, _, meta = _collect(_Fixture(), max_pages=2)
    assert rows == []
    assert meta["source_cap_reached"] is True

    rows, _, meta = _collect(_Fixture(), detail_limit=0)
    assert rows == []
    assert meta["source_cap_reached"] is True

    rows, _, meta = _collect(_Fixture(), dedupe_rows=lambda values: [])
    assert rows == []
    assert "dedupe_rows changed" in meta["configured_collection_error"]


def test_exact_dispatch_target_operational_and_coverage_linkage(monkeypatch) -> None:
    expected = ([{"provider": sangju.SANGJU_EXPERIENCE_PROVIDER}], "parser", {"ok": True})
    captured: dict[str, object] = {}

    def fake_collect(target: object, **kwargs: object):
        assert target == _DispatchTarget()
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(sangju, "collect_sangju_experience", fake_collect)
    assert (
        municipal.collect_from_url(_DispatchTarget(), timeout=7, max_depth=0, max_pages=10, detail_limit=11) == expected
    )
    assert captured["timeout"] == 7
    assert captured["max_pages"] == 10
    assert captured["detail_limit"] == 11

    root = municipal.ROOT / "config"
    targets = yaml.safe_load((root / "crawl_targets" / "public_reservation.yaml").read_text(encoding="utf-8"))[
        "targets"
    ]
    matches = [row for row in targets if row.get("provider") == sangju.SANGJU_EXPERIENCE_PROVIDER]
    assert len(matches) == 1
    assert matches[0]["url"] == sangju.SANGJU_EXPERIENCE_CANONICAL_URL
    assert matches[0]["crawler_status"] == "ready"
    assert matches[0]["full_snapshot_required"] is True
    assert matches[0]["service_group"] == "체험"
    assert matches[0]["ops_scopes"] == ["experience"]

    operational = yaml.safe_load(
        (root / "municipal_integrated_reservation_operational.yaml").read_text(encoding="utf-8")
    )["entries"]
    operational_matches = [row for row in operational if row.get("provider") == sangju.SANGJU_EXPERIENCE_PROVIDER]
    assert len(operational_matches) == 1
    assert operational_matches[0]["row_count"] == 1

    coverage = yaml.safe_load((root / "municipal_integrated_reservation_coverage.yaml").read_text(encoding="utf-8"))[
        "municipalities"
    ]
    sangju_coverage = next(row for row in coverage if row.get("code") == "4725000000")
    assert sangju.SANGJU_EXPERIENCE_PROVIDER in sangju_coverage["owner_providers"]
    assert sangju.SANGJU_EXPERIENCE_PROVIDER in sangju_coverage["promoted_providers"]
    assert sangju.SANGJU_EXPERIENCE_PROVIDER in sangju_coverage["yaml_owner_providers"]


@pytest.mark.skipif(
    os.getenv("RUN_SANGJU_EXPERIENCE_LIVE") != "1",
    reason="set RUN_SANGJU_EXPERIENCE_LIVE=1 for the bounded official snapshot",
)
def test_live_exact_snapshot() -> None:
    rows, parser, meta = sangju.collect_sangju_experience(
        _target(),
        today="2026-08-05",
        timeout=30,
        max_pages=sangju.SANGJU_EXPERIENCE_RECOMMENDED_MAX_PAGES,
        detail_limit=sangju.SANGJU_EXPERIENCE_RECOMMENDED_DETAIL_LIMIT,
        allow_raw_requests_for_tests=True,
    )
    baseline = sangju.SANGJU_EXPERIENCE_LIVE_AUDIT_BASELINE
    assert parser == sangju.SANGJU_EXPERIENCE_PARSER
    assert meta["configured_collection_error"] == ""
    assert len(rows) == baseline["current_total"]
    assert meta["source_total_count"] == baseline["source_total"]
    assert meta["standing_source_count"] == baseline["standing_source_total"]
    assert meta["expired_source_count"] == baseline["expired_source_total"]
    assert meta["page_sizes"] == baseline["page_sizes"]
    assert meta["post_last_page"] == baseline["post_last_page"]
    assert meta["facility_partition_counts"] == baseline["facility_counts"]
    assert meta["facility_partition_pages"] == baseline["facility_pages"]
    assert meta["current_facility_counts"] == baseline["current_facility_counts"]
    assert meta["status_counts"] == baseline["status_counts"]
    assert meta["current_ids"] == baseline["current_ids"]
    assert meta["identity_first"] == baseline["identity_first"]
    assert meta["identity_last"] == baseline["identity_last"]
    assert meta["list_requests"] == baseline["list_requests"]
    assert meta["detail_requests"] == baseline["detail_requests"]
    assert meta["source_requests"] == baseline["source_requests"]
    assert meta["application_endpoint_requests"] == 0
    assert meta["attachment_endpoint_requests"] == 0
    assert rows[0]["raw_fields"]["identity"] == "100609"
