from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import math
import os
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_sangju as sangju


class _Response:
    def __init__(self, url: str, html: str, status_code: int = 200):
        self.url = url
        self.content = html.encode("utf-8")
        self.status_code = status_code
        self.history: list[object] = []


class _Session:
    def __init__(self, scenario: "_Scenario") -> None:
        self.scenario = scenario
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target() -> dict[str, str]:
    return {"provider": sangju.SANGJU_PROVIDER, "url": sangju.SANGJU_CANONICAL_URL}


def _facilities() -> str:
    parts = ['<li><button type="button" onclick="reserveList.searchFacility(\'\');">전체</button></li>']
    for facility in sangju.SANGJU_FACILITIES:
        parts.append(
            "<li><button type=\"button\" "
            f"onclick=\"reserveList.searchFacility('{facility.code}');\">"
            f"{facility.name}</button></li>"
        )
    return '<ul class="com_tab com_tab2">' + "".join(parts) + "</ul>"


def _selected_facilities(code: str, *, registry_drift: bool = False) -> str:
    buttons = []
    entries = [("", "전체"), *((item.code, item.name) for item in sangju.SANGJU_FACILITIES)]
    if registry_drift:
        entries = entries[:-1]
    for item_code, name in entries:
        css = ' class="active"' if item_code == code else ""
        buttons.append(
            f"<li{css}><button type=\"button\" "
            f"onclick=\"reserveList.searchFacility('{item_code}');\">"
            f"{name}</button></li>"
        )
    return '<ul class="com_tab com_tab2">' + "".join(buttons) + "</ul>"


def _shell(body: str) -> str:
    return (
        "<html><head><title>통합예약 홈페이지 &gt; 교육·강좌 &gt; 온라인예약 | 상주시</title></head>"
        f"<body>{body}<footer><span>(37211) 경상북도 상주시 상산로 223(남성동 140-3)"
        "</span></footer></body></html>"
    )


def _count_field(label: str, current: int, total: int) -> str:
    return f"<li><span>{label}</span><p><em>{current}</em>/{total}명</p></li>"


def _reception_block(item: dict[str, object], namespace: str) -> str:
    if item["status"] == "예약불가":
        return (
            '<li><ul class="tm_cir"><li><span class="color_red">[예약불가]</span>'
            "등록된 접수 정보가 없습니다.</li></ul></li>"
        )
    fields = [
        f"<li><span>접수기간</span>{item['apply_period']}</li>"
    ]
    shape = int(item.get("shape", 3))
    if shape >= 2:
        fields.append(
            _count_field("정원", int(item.get("capacity_current", 1)), int(item.get("capacity_total", 10)))
        )
    if shape >= 3:
        fields.append(
            _count_field("후보", int(item.get("wait_current", 0)), int(item.get("wait_total", 3)))
        )
    status = str(item["status"])
    if status == "예약":
        action = f"{namespace}.apply('{item['rcpt_no']}');"
        anchor = f'<a href="javascript:;" onclick="{action}">예약</a>'
    elif status == "대기":
        anchor = (
            '<a class="bg_gray" href="javascript:;" '
            "onclick=\"alert('접수 대기 중입니다.');\">대기</a>"
        )
    else:
        anchor = (
            '<a class="bg_dark" href="javascript:;" '
            "onclick=\"alert('접수가 종료되었습니다.')\">종료</a>"
        )
    return (
        f"<li><h2>{item['heading']}</h2><ul class=\"tm_cir\">"
        + "".join(fields)
        + f"</ul>{anchor}</li>"
    )


def _normal_reception(
    status: str,
    number: int,
    *,
    heading: str = "온라인 접수",
    shape: int = 3,
    standing: bool = False,
) -> dict[str, object]:
    period = (
        "2025년 01월 16일 10시 25분 ~ 상시운영"
        if standing
        else (
            "2026년 08월 01일 09시 00분 ~ 2026년 08월 10일 18시 00분"
            if status == "대기"
            else "2026년 07월 01일 09시 00분 ~ 2026년 07월 31일 18시 00분"
            if status == "예약"
            else "2026년 06월 01일 09시 00분 ~ 2026년 06월 30일 18시 00분"
        )
    )
    return {
        "status": status,
        "heading": heading,
        "apply_period": period,
        "shape": shape,
        "capacity_current": number % 5,
        "capacity_total": 10,
        "wait_current": 0,
        "wait_total": 3,
        "rcpt_no": str(700000 + number) if status == "예약" else "",
    }


def _records() -> list[dict[str, object]]:
    current_statuses = ["대기", "예약", "종료", "정보제공", "대기", "예약", "정보제공"]
    current_facilities = ["84", "84", "84", "84", "131", "129", "100006"]
    records: list[dict[str, object]] = []
    for index, (status, facility_code) in enumerate(zip(current_statuses, current_facilities)):
        facility = sangju.SANGJU_FACILITY_BY_CODE[facility_code]
        receptions = [] if status == "정보제공" else [_normal_reception(status, index + 1)]
        records.append(
            {
                "identity": str(900019 - index),
                "title": f"현재 교육 {index + 1:02d}",
                "facility_code": facility_code,
                "facility": facility.name,
                "address": f"경북 상주시 합성로 {index + 1}",
                "period": f"2026-08-{index + 1:02d} ~ 2026-12-{index + 1:02d}",
                "detail_period": (
                    f"2026년 08월 {index + 1:02d}일 10시 00분 ~ "
                    f"2026년 12월 {index + 1:02d}일 12시 00분"
                ),
                "status": status,
                "receptions": receptions,
            }
        )

    historical: list[tuple[str, list[dict[str, object]], str]] = [
        ("예약", [_normal_reception("예약", 8, shape=1, standing=True)], "2026-01-01 ~ 상시운영"),
        ("예약불가", [{"status": "예약불가"}], "2025-05-01 ~ 2025-05-02"),
        ("종료", [_normal_reception("종료", 90), _normal_reception("종료", 91)], "2025-04-01 ~ 2025-04-02"),
        (
            "종료",
            [_normal_reception("종료", number) for number in range(100, 104)],
            "2025-03-01 ~ 2025-03-02",
        ),
        ("종료", [_normal_reception("종료", 11, shape=2)], "2025-02-01 ~ 2025-02-02"),
        ("예약", [_normal_reception("예약", 12, shape=1)], "2025-01-01 ~ 2025-01-02"),
    ]
    other_codes = ["100001", "130", "132", "135", "106", "119", "113"]
    for offset in range(12):
        index = 7 + offset
        facility_code = "84" if offset < 5 else other_codes[offset - 5]
        facility = sangju.SANGJU_FACILITY_BY_CODE[facility_code]
        if offset < len(historical):
            status, receptions, period = historical[offset]
        else:
            status = "종료" if offset % 2 else "정보제공"
            receptions = [_normal_reception("종료", 200 + offset)] if status == "종료" else []
            period = f"2024-01-{offset + 1:02d} ~ 2024-02-{offset + 1:02d}"
        records.append(
            {
                "identity": str(900019 - index),
                "title": f"과거 교육 {offset + 1:02d}",
                "facility_code": facility_code,
                "facility": facility.name,
                "address": f"경북 상주시 과거로 {offset + 1}",
                "period": period,
                "detail_period": "",
                "status": status,
                "receptions": receptions,
            }
        )
    return records


_ROWS = _records()


def _badge(status: str) -> str:
    if status == "예약":
        return "<span>온라인예약 접수중</span>"
    if status == "정보제공":
        return '<span class="bg_sky2">정보제공</span>'
    return ""


def _card(record: dict[str, object], ordinal: int, namespace: str = "reserveList") -> str:
    identity = str(record["identity"])
    receptions = list(record["receptions"])
    reception_html = ""
    if receptions:
        reception_html = (
            '<ul class="list_sub mb">'
            + "".join(_reception_block(item, namespace) for item in receptions)
            + "</ul>"
        )
    return f"""
    <section>
      <div class="flex"><div class="right">
        <div class="top">{_badge(str(record['status']))}</div>
        <h1><em>{ordinal:02d}</em><a href="#none" onclick="reserveList.detail('{identity}');">{record['title']}</a></h1>
        <ul class="tm_cir">
          <li><span>분류</span>교육/강좌 (합성교육)</li>
          <li><span>시설명</span>{record['facility']}</li>
          <li><span>운영기간</span>{record['period']}</li>
          <li><span>주소</span>{record['address']}</li>
        </ul>
        <a href="javascript:;" onclick="reserveList.detail('{identity}')">상세보기</a>
      </div></div>
      {reception_html}
    </section>
    """


def _pager(page: int, last: int, *, sentinel_active: bool = False) -> str:
    active = f'<a class="active">{page}</a>' if page <= last or sentinel_active else ""
    return (
        '<ul class="pager">'
        f"{active}<a class=\"pager_arrow pager_next_all\" href=\"?pageIndex={last}\" "
        f"onclick=\"reserveList.pageMove({last}); return false;\">&gt;&gt;</a></ul>"
    )


def _list_html(
    records: list[dict[str, object]],
    page: int,
    facility_code: str,
    *,
    registry_drift: bool = False,
    ordinal_gap: bool = False,
    sentinel_active: bool = False,
) -> str:
    total = len(records)
    last = max(1, math.ceil(total / sangju.SANGJU_PAGE_SIZE))
    start = (page - 1) * sangju.SANGJU_PAGE_SIZE
    selected = records[start : start + sangju.SANGJU_PAGE_SIZE]
    cards = []
    for position, record in enumerate(selected):
        ordinal = total - start - position
        if ordinal_gap and position == 1:
            ordinal -= 1
        cards.append(_card(record, ordinal))
    ledger = (
        '<div class="list3" id="reserveList">'
        + ("".join(cards) if cards else '<section><p class="no_data">자료가 없습니다.</p></section>')
        + "</div>"
    )
    form = f"""
      <form id="reserveListForm" name="reserveListForm">
        <input type="hidden" name="pageNo" value="11881">
        <input type="hidden" name="mn" value="15375">
        <input type="hidden" name="pageIndex" value="{page}">
        <input type="hidden" name="searchTrgtClsfCd" value="RMS004001">
        <input type="hidden" name="searchFcltNo" value="{facility_code}">
        <input type="hidden" name="cyclNo" value="">
        <input type="hidden" name="rcptNo" value="">
        {_selected_facilities(facility_code, registry_drift=registry_drift)}
        {ledger}{_pager(page, last, sentinel_active=sentinel_active)}
      </form>
    """
    return _shell(form)


def _detail_html(
    record: dict[str, object],
    *,
    wrong_title: bool = False,
    wrong_state: bool = False,
    wrong_reception: bool = False,
) -> str:
    identity = str(record["identity"])
    title = str(record["title"]) + (" 변경" if wrong_title else "")
    state = {
        "예약": "온라인예약 접수중",
        "대기": "온라인예약 준비중",
        "종료": "온라인예약 준비중",
        "정보제공": "정보제공",
    }[str(record["status"])]
    if wrong_state:
        state = "정보제공"
    receptions = [dict(item) for item in record["receptions"]]
    if wrong_reception and receptions:
        receptions[0]["heading"] = "변경된 접수반"
    motion = ""
    if receptions:
        motion = (
            '<div class="motion_wrap"><ul class="list_sub">'
            + "".join(_reception_block(item, "reserveDetail") for item in receptions)
            + "</ul></div>"
        )
    form = f"""
      <form id="reserveDetailForm" name="reserveDetailForm">
        <input type="hidden" name="pageNo" value="11881">
        <input type="hidden" name="mn" value="15375">
        <input type="hidden" name="pageIndex" value="">
        <input type="hidden" name="searchTrgtClsfCd" value="RMS004001">
        <input type="hidden" name="searchFcltNo" value="">
        <input type="hidden" name="cyclNo" value="{identity}">
        <input type="hidden" name="rcptNo" value="">
        <div class="img_jb"><div class="right">
          <div class="top"><span>{state}</span></div><h1>{title}</h1>
          <ul class="tm_cir">
            <li><span>분류</span>교육/강좌</li>
            <li><span>시설명</span>{record['facility']}</li>
            <li><span>주소<button type="button">주소복사</button></span>{record['address']}</li>
            <li><span>운영기간</span>{record['detail_period']}</li>
            <li><span>강사</span>저장하면 안 되는 강사명</li>
          </ul>
        </div></div>
        {motion}
        <div class="tabpanel_wrap">
          <div class="bd_scroll">문의 054-123-4567, private@example.com</div>
          <div class="bd_scroll"><a href="/file/readFile.tc?fileId=PRIVATE">첨부</a>무료</div>
          <div class="bd_scroll">상세 자유서술 본문</div>
        </div>
        <img src="/file/readFile.tc?scale=PRIVATE">
        <div class="bot_btn"><a href="javascript:;" onclick="reserveDetail.list();">목록으로</a></div>
      </form>
    """
    return _shell(form)


class _Scenario:
    def __init__(
        self,
        *,
        duplicate_identity: bool = False,
        ordinal_gap: bool = False,
        registry_drift: bool = False,
        partition_drop: bool = False,
        partition_drift: bool = False,
        detail_title_drift: bool = False,
        detail_state_drift: bool = False,
        detail_reception_drift: bool = False,
        boundary_drift: bool = False,
        sentinel_drift: bool = False,
        response_url_drift: bool = False,
    ) -> None:
        self.duplicate_identity = duplicate_identity
        self.ordinal_gap = ordinal_gap
        self.registry_drift = registry_drift
        self.partition_drop = partition_drop
        self.partition_drift = partition_drift
        self.detail_title_drift = detail_title_drift
        self.detail_state_drift = detail_state_drift
        self.detail_reception_drift = detail_reception_drift
        self.boundary_drift = boundary_drift
        self.sentinel_drift = sentinel_drift
        self.response_url_drift = response_url_drift
        self.requests: list[str] = []
        self.sessions: list[_Session] = []
        self._counts: Counter[tuple[str, str]] = Counter()
        self._lock = Lock()

    def session_factory(self) -> _Session:
        session = _Session(self)
        self.sessions.append(session)
        return session

    def fetch(self, _session: _Session, url: str, _timeout: int) -> _Response:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        with self._lock:
            self.requests.append(url)
        actual_url = url + "&unexpected=1" if self.response_url_drift and len(self.requests) == 1 else url
        if parsed.path == sangju.SANGJU_LIST_PATH:
            page = int(query["pageIndex"][0])
            code = query["searchFcltNo"][0]
            key = (code, str(page))
            self._counts[key] += 1
            records = [dict(item) for item in _ROWS if not code or item["facility_code"] == code]
            if self.duplicate_identity and not code:
                records[1]["identity"] = records[0]["identity"]
            if self.partition_drop and code == "131":
                records = records[1:]
            if self.partition_drift and code == "129" and records:
                records[0]["title"] = str(records[0]["title"]) + " 변경"
            if self.boundary_drift and not code and page == 1 and self._counts[key] > 1:
                records[0]["title"] = str(records[0]["title"]) + " 재수집변경"
            html = _list_html(
                records,
                page,
                code,
                registry_drift=self.registry_drift,
                ordinal_gap=self.ordinal_gap and not code and page == 1,
                sentinel_active=self.sentinel_drift and not code and page == 4,
            )
            return _Response(actual_url, html)
        if parsed.path == sangju.SANGJU_DETAIL_PATH:
            identity = query["cyclNo"][0]
            record = next(item for item in _ROWS if item["identity"] == identity)
            return _Response(
                actual_url,
                _detail_html(
                    record,
                    wrong_title=self.detail_title_drift and identity == _ROWS[0]["identity"],
                    wrong_state=self.detail_state_drift and identity == _ROWS[0]["identity"],
                    wrong_reception=(
                        self.detail_reception_drift and identity == _ROWS[0]["identity"]
                    ),
                ),
            )
        raise AssertionError(f"forbidden endpoint requested: {url}")


def _collect(scenario: _Scenario, **kwargs: object):
    options: dict[str, object] = {
        "today": "2026-07-23",
        "max_pages": 10,
        "detail_limit": 20,
        "session_factory": scenario.session_factory,
        "fetcher": scenario.fetch,
    }
    options.update(kwargs)
    return sangju.collect_sangju_education(_target(), **options)


def test_exact_owner_hashes_aliases_and_target_matcher() -> None:
    assert hashlib.sha1(sangju.SANGJU_CANONICAL_URL.encode()).hexdigest() == sangju.SANGJU_CANONICAL_URL_SHA1
    assert hashlib.sha256(sangju.SANGJU_CANONICAL_URL.encode()).hexdigest() == sangju.SANGJU_CANONICAL_URL_SHA256
    assert sangju.SANGJU_CANONICAL_CANDIDATE_ID == "MUNI_IR_482FB9F1BE5F"
    assert sangju.SANGJU_DISCOVERY_DETAIL_CANDIDATE_ID == "MUNI_IR_1562731F2A97"
    assert sangju.SANGJU_PROVIDER_ALIAS_AUDIT[sangju.SANGJU_DUPLICATE_RESERVATION_PROVIDER]["state"] == "superseded"
    assert sangju.SANGJU_PROVIDER_ALIAS_AUDIT[sangju.SANGJU_LEGACY_LIFELONG_PROVIDER]["state"] == "superseded"
    assert sangju.is_target(_target())
    assert not sangju.is_target({**_target(), "url": sangju.SANGJU_DUPLICATE_RESERVATION_URL})
    assert not sangju.is_target({**_target(), "url": sangju.SANGJU_CANONICAL_URL + "&x=1"})
    assert not sangju.is_target({**_target(), "provider": sangju.SANGJU_DUPLICATE_RESERVATION_PROVIDER})


def test_complete_snapshot_partitions_details_statuses_and_privacy() -> None:
    scenario = _Scenario()
    rows, parser, meta = _collect(scenario)
    assert parser == sangju.SANGJU_PARSER
    assert not meta["configured_collection_error"]
    assert meta["snapshot_complete"] is True
    assert len(rows) == meta["row_count"] == meta["current_source_count"] == 7
    assert meta["source_total_count"] == 19
    assert meta["standing_source_count"] == 1
    assert meta["expired_source_count"] == 11
    assert meta["data_pages"] == 3
    assert meta["page_sizes"] == [8, 8, 3]
    assert meta["post_last_page"] == 4
    assert meta["list_requests"] == 20
    assert meta["detail_requests"] == 7
    assert meta["source_requests"] == 27
    assert meta["facility_filter_requests"] == 13
    assert meta["full_recheck_requests"] == 3
    assert sum(meta["facility_partition_counts"].values()) == 19
    assert meta["facility_partition_counts"]["84"] == 9
    assert meta["facility_partition_counts"]["127"] == 0
    assert meta["facility_partition_pages"]["84"] == 2
    assert meta["facility_partition_union_count"] == 19
    assert meta["facility_partition_overlap_count"] == 0
    assert meta["status_counts"] == {"SCHEDULED": 2, "OPEN": 2, "CLOSED": 3}
    assert meta["application_control_count"] == 2
    assert meta["attachment_links_discarded"] == 7
    assert meta["images_discarded"] == 7
    assert meta["instructor_fields_discarded"] == 7
    assert meta["free_text_panels_discarded"] == 21
    assert all(session.closed for session in scenario.sessions)
    assert len(scenario.sessions) == 1
    assert not any(urlparse(url).path == sangju.SANGJU_APPLICATION_PATH for url in scenario.requests)
    assert not any("readFile.tc" in url for url in scenario.requests)
    assert [row["raw_fields"]["identity"] for row in rows] == [str(item["identity"]) for item in _ROWS[:7]]
    assert Counter(row["branch_code"] for row in rows) == {"84": 4, "131": 1, "129": 1, "100006": 1}
    for row in rows:
        assert row["provider"] == sangju.SANGJU_PROVIDER
        assert row["provider_course_id"].endswith(":cycl:" + row["raw_fields"]["identity"])
        assert row["description"] == row["title"]
        assert row["collection_category"] == "공공예약"
        assert row["domain_category"] == "교육·강좌"
        assert row["source_group"] == "municipal_reservation"
        assert row["service_group"] == "공공강좌"
        assert row["raw_url"].startswith("https://www.sangju.go.kr/reserve/reservation/detail.tc?")
        payload = repr(row)
        assert "저장하면 안 되는 강사명" not in payload
        assert "054-123-4567" not in payload
        assert "private@example.com" not in payload
        assert "상세 자유서술 본문" not in payload
        assert "fileId=PRIVATE" not in payload
        assert row["raw_fields"]["application_endpoint_fetched"] is False
        assert row["raw_fields"]["attachment_endpoint_fetched"] is False
        assert row["raw_fields"]["free_text_persisted"] is False
        if row["status"] == "OPEN":
            assert row["application_url"].startswith(
                "https://www.sangju.go.kr/reserve/reservation/apply.tc?"
            )
        else:
            assert row["application_url"] == ""


def test_historical_reception_variants_are_audited_without_becoming_rows() -> None:
    scenario = _Scenario()
    rows, _, meta = _collect(scenario)
    assert len(rows) == 7
    assert meta["source_raw_status_counts"]["예약불가"] == 1
    assert meta["source_raw_status_counts"]["예약"] == 4
    assert meta["standing_source_count"] == 1
    assert all(not row["raw_fields"]["identity"].endswith("12") for row in rows)
    with pytest.raises(sangju.SangjuContractError, match="reversed operating period"):
        sangju._parse_iso_period("2025-02-02 ~ 2025-01-01", "999")
    assert sangju._parse_iso_period("2024-08-24 ~ 2024-08-13", "414") == (
        date(2024, 8, 24),
        date(2024, 8, 13),
    )


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"duplicate_identity": True}, "identity"),
        ({"ordinal_gap": True}, "ordinal"),
        ({"registry_drift": True}, "facility registry"),
        ({"partition_drop": True}, "partition union incomplete"),
        ({"partition_drift": True}, "partition data drift"),
        ({"detail_title_drift": True}, "list/detail structured data drift"),
        ({"detail_state_drift": True}, "list/detail state drift"),
        ({"detail_reception_drift": True}, "list/detail reception drift"),
        ({"boundary_drift": True}, "source boundaries changed"),
        ({"sentinel_drift": True}, "post-last page"),
        ({"response_url_drift": True}, "response URL drift"),
    ],
)
def test_contract_drift_fails_atomically(kwargs: dict[str, bool], fragment: str) -> None:
    rows, _, meta = _collect(_Scenario(**kwargs))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert fragment in meta["configured_collection_error"]


def test_limits_managed_session_and_dedupe_cardinality_fail_closed() -> None:
    rows, _, meta = sangju.collect_sangju_education(_target(), today="2026-07-23")
    assert rows == []
    assert "managed session_factory" in meta["configured_collection_error"]

    rows, _, meta = _collect(_Scenario(), max_pages=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages" in meta["configured_collection_error"]

    rows, _, meta = _collect(_Scenario(), detail_limit=6)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit" in meta["configured_collection_error"]

    rows, _, meta = _collect(_Scenario(), dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe_rows changed" in meta["configured_collection_error"]

    rows, _, meta = _collect(_Scenario(), dedupe_rows=lambda values: reversed(values))
    assert len(rows) == 7
    assert meta["snapshot_complete"] is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"timeout": 0},
        {"timeout": True},
        {"max_pages": 0},
        {"detail_limit": -1},
        {"today": "not-a-date"},
    ],
)
def test_invalid_options_fail_before_request(overrides: dict[str, object]) -> None:
    scenario = _Scenario()
    kwargs = {
        "today": "2026-07-23",
        "max_pages": 10,
        "detail_limit": 20,
        "session_factory": scenario.session_factory,
        "fetcher": scenario.fetch,
        **overrides,
    }
    rows, _, meta = sangju.collect_sangju_education(_target(), **kwargs)
    assert rows == []
    assert meta["configured_collection_error"]
    assert scenario.requests == []


def test_wrong_target_is_rejected_before_session_creation() -> None:
    scenario = _Scenario()
    rows, _, meta = sangju.collect_sangju_education(
        {**_target(), "url": sangju.SANGJU_DUPLICATE_RESERVATION_URL},
        session_factory=scenario.session_factory,
        fetcher=scenario.fetch,
    )
    assert rows == []
    assert "exact retained Sangju owner" in meta["configured_collection_error"]
    assert scenario.sessions == []


@pytest.mark.skipif(
    os.getenv("RUN_SANGJU_LIVE") != "1",
    reason="set RUN_SANGJU_LIVE=1 for two bounded official-source snapshots",
)
def test_live_two_exact_stable_snapshots() -> None:
    snapshots = []
    for _ in range(2):
        rows, parser, meta = sangju.collect_sangju_education(
            _target(),
            today="2026-07-23",
            timeout=30,
            max_pages=sangju.SANGJU_RECOMMENDED_MAX_PAGES,
            detail_limit=sangju.SANGJU_RECOMMENDED_DETAIL_LIMIT,
            allow_raw_requests_for_tests=True,
        )
        assert parser == sangju.SANGJU_PARSER
        assert meta["configured_collection_error"] == ""
        assert meta["snapshot_complete"] is True
        assert len(rows) == sangju.SANGJU_LIVE_AUDIT_BASELINE["current_total"]
        assert meta["source_total_count"] == sangju.SANGJU_LIVE_AUDIT_BASELINE["source_total"]
        assert meta["standing_source_count"] == sangju.SANGJU_LIVE_AUDIT_BASELINE["standing_source_total"]
        assert meta["data_pages"] == sangju.SANGJU_LIVE_AUDIT_BASELINE["data_pages"]
        assert meta["page_sizes"] == sangju.SANGJU_LIVE_AUDIT_BASELINE["page_sizes"]
        assert meta["facility_partition_counts"] == sangju.SANGJU_LIVE_AUDIT_BASELINE["facility_counts"]
        assert meta["facility_partition_pages"] == sangju.SANGJU_LIVE_AUDIT_BASELINE["facility_pages"]
        assert meta["current_facility_counts"] == sangju.SANGJU_LIVE_AUDIT_BASELINE["current_facility_counts"]
        assert meta["status_counts"] == sangju.SANGJU_LIVE_AUDIT_BASELINE["status_counts"]
        assert meta["current_ids"] == sangju.SANGJU_LIVE_AUDIT_BASELINE["current_ids"]
        assert meta["list_requests"] == sangju.SANGJU_LIVE_AUDIT_BASELINE["list_requests"]
        assert meta["detail_requests"] == sangju.SANGJU_LIVE_AUDIT_BASELINE["detail_requests"]
        assert meta["source_requests"] == sangju.SANGJU_LIVE_AUDIT_BASELINE["source_requests"]
        assert meta["application_endpoint_requests"] == 0
        assert meta["attachment_endpoint_requests"] == 0
        assert meta["application_form_submissions"] == 0
        snapshots.append(rows)
    assert snapshots[0] == snapshots[1]
