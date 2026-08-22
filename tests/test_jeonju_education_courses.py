from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
import requests

from Crawler import municipal_jeonju as jeonju


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class JeonjuTarget:
    provider: str = jeonju.JEONJU_PROVIDER
    url: str = jeonju.JEONJU_CANONICAL_URL
    branch: str = jeonju.JEONJU_BRANCH


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _factory() -> tuple[Any, list[DummySession]]:
    sessions: list[DummySession] = []

    def make_session() -> DummySession:
        current = DummySession()
        sessions.append(current)
        return current

    return make_session, sessions


def _pager(page: int, last: int, gc: str) -> str:
    if last <= 1:
        return ""
    links = []
    for value in range(1, last + 1):
        css = " class='on'" if value == page else ""
        href = "#" if value == page else f"?gc={gc}&do=list&page={value}"
        links.append(f"<a{css} href='{href}'>{value}</a>")
    links.append(
        f"<a class='parrow04' href='?gc={gc}&do=list&page={last}' "
        "title='마지막'></a>"
    )
    return "<div class='page_box'>" + "".join(links) + "</div>"


def _card(
    record: dict[str, Any],
    catalogue: jeonju.JeonjuCatalogue,
    page: int,
) -> str:
    detail_url = jeonju.jeonju_detail_url(catalogue, record["id"], page)
    return f"""
      <li><a class='Fix_ListBtns' href='{detail_url}'>
        <div class='cont'>
          <div class='tit'><span class='cate'>[{record['category']}]</span>
            <!-- 유,무료 구분 -->{record['title']}<!-- 상태 배지 -->
            <span class='program_ptype01'>{record['fee_type']}</span>
          </div>
          <div class='txt'>
            <p>진행기간 : {record['period']}</p>
            <p>신청기간 : {record['apply_period']}</p>
            <p>대상 : {record['target']}</p>
            <p>정원 : {record['capacity']}명</p>
            <p>신청가능 : 일반회원</p>
          </div>
        </div>
        <div class='btn'><span class='state_end'>{record['source_status']}</span></div>
      </a></li>
    """


def _list_page(
    records: list[dict[str, Any]],
    catalogue: jeonju.JeonjuCatalogue,
    *,
    page: int,
    total: int,
) -> str:
    last = max(1, (total + jeonju.JEONJU_PAGE_SIZE - 1) // jeonju.JEONJU_PAGE_SIZE)
    return f"""
      <html><body><article>
        <h3>{catalogue.name}</h3>
        <div class='ginfo_box'><div class='ginfo'>전체 <span>{total}</span> 건</div></div>
        <ul class='class_list_wrap'>{''.join(_card(row, catalogue, page) for row in records)}</ul>
        {_pager(page, last, catalogue.gc)}
      </article></body></html>
    """


def _detail_page(
    record: dict[str, Any],
    catalogue: jeonju.JeonjuCatalogue,
    *,
    missing_address: bool = False,
    missing_control: bool = False,
    auth_suppressed: bool = False,
    missing_schedule: bool = False,
    offhost_control: bool = False,
    bad_title: bool = False,
) -> str:
    fields = [
        ("강좌분류", record["category"]),
        ("대상", record["target"]),
        ("강의일시", "매주 월 10:00"),
        ("강의기간", "10주"),
        ("진행기간", record["period"]),
        ("신청기간", record["apply_period"]),
        ("수강료", "30,000원" if record["fee_type"] == "유료" else "무료"),
        ("정원", f"{record['capacity']}명"),
        ("문의", "063-281-5269"),
        ("신청방법", "방문접수, 온라인"),
        ("신청가능", "일반회원"),
        ("강사명", "테스트강사"),
    ]
    if missing_schedule:
        fields = [
            field
            for field in fields
            if field[0] not in {"강의일시", "강의기간"}
        ]
    if not missing_address:
        fields.insert(
            9,
            (
                "교육장 주소",
                "[55020] 전북특별자치도 전주시 덕진구 구총목로11",
            ),
        )
    controls = ""
    if record.get("control") and not missing_control:
        application = (
            f"https://example.com/main/menu?gc={catalogue.gc}&do=sinform"
            f"&program_id={record['id']}&psin_id=PSIN_{record['id']}"
            if offhost_control
            else (
                f"https://e.jeonju.go.kr/main/menu?gc={catalogue.gc}&do=sinform"
                f"&program_id={record['id']}&psin_id=PSIN_{record['id']}"
            )
        )
        controls = (
            f"<a class='course_apply' href='{application.replace('&', '&amp;')}'>"
            f"{record['control']}</a>"
        )
    login = (
        "<a href='https://e.jeonju.go.kr/main/menu?gc=LOGIN'>로그인</a>"
        if auth_suppressed
        else ""
    )
    detail_title = "다른 강좌" if bad_title else record["title"]
    dls = "".join(f"<dl><dt>{key}</dt><dd>{value}</dd></dl>" for key, value in fields)
    return f"""
      <html><body><article>
        <div class='class_view_wrap'><div class='inner'>
          <p class='tit'><span class='state_end'>{record['source_status']}</span>
            <strong>{detail_title}</strong></p>
          <div class='cont'>{dls}</div>
        </div></div>
        <div class='program_viewbox'>강사 개인정보와 자유 서술은 저장하지 않는다.</div>
        {controls}{login}
      </article></body></html>
    """


def _record(
    identity: str,
    title: str,
    *,
    source_status: str = "접수마감",
    period: str = "2099.07.21(화) ~ 2099.08.31(월)",
    apply_period: str = "2099.06.01(월) ~ 2099.06.30(화)",
    control: str = "",
) -> dict[str, Any]:
    return {
        "id": identity,
        "title": title,
        "category": "인문교양",
        "fee_type": "유료",
        "period": period,
        "apply_period": apply_period,
        "target": "전체",
        "capacity": 20,
        "source_status": source_status,
        "control": control,
    }


def _fixture(
    *,
    empty: bool = False,
    bad_sentinel_gc: str = "",
    duplicate_identity: bool = False,
    missing_address_gc: str = "",
    missing_control_gc: str = "",
    auth_suppressed_gc: str = "",
    missing_schedule_gc: str = "",
    offhost_control_gc: str = "",
    bad_title_gc: str = "",
    bad_total_gc: str = "",
    unknown_status_gc: str = "",
) -> tuple[Any, Any, list[DummySession], dict[str, list[dict[str, Any]]]]:
    pages: dict[str, str] = {}
    records_by_gc: dict[str, list[dict[str, Any]]] = {}

    for catalogue in jeonju.JEONJU_CATALOGUES:
        if empty:
            records: list[dict[str, Any]] = []
        elif catalogue.gc == "Program21":
            records = [
                _record(f"P21_{value:02d}_IDENTITY", f"시민강좌 {value}")
                for value in range(1, 14)
            ]
        elif catalogue.gc == "Program22":
            records = [
                _record(
                    "P22_EXPIRED_ID",
                    "종료된 쌈지교실",
                    source_status="종료",
                    period="2099.06.01(월) ~ 2099.06.02(화)",
                    apply_period="2099.05.01(금) ~ 2099.05.31(일)",
                )
            ]
        elif catalogue.gc == "Program23":
            records = [_record("P23_CURRENT_ID", "현재 인문학")]
        elif catalogue.gc == "Program24":
            records = [
                _record(
                    "P24_OPEN_ID",
                    "접수중 오십플러스",
                    source_status="신청하기",
                    period="2099.07.21(화) ~ 2099.12.05(토)",
                    apply_period="2099.07.01(수) ~ 2099.07.30(목)",
                    control="신청하기",
                )
            ]
        elif catalogue.gc == "Program25":
            records = [
                _record(
                    "P25_WAIT_ID",
                    "대기자 모두배움터",
                    source_status="대기자접수",
                    period="2099.07.21(화) ~ 2099.12.05(토)",
                    apply_period="2099.07.01(수) ~ 2099.07.30(목)",
                    control="대기자접수",
                )
            ]
        elif catalogue.gc == "Program26":
            records = [
                _record(
                    "P26_SCHEDULED_ID",
                    "접수예정 기타강좌",
                    source_status="접수예정",
                    period="2099.08.20(목) ~ 2099.09.20(일)",
                    apply_period="2099.08.01(토) ~ 2099.08.10(월)",
                )
            ]
        else:
            records = [
                _record(
                    "P27_EXPIRED_ID",
                    "종료된 열린시민강좌",
                    source_status="종료",
                    period="2099.07.16(목) 19:00 ~ 21:00",
                    apply_period="2099.06.24(수) ~ 2099.07.15(수)",
                )
            ]

        if duplicate_identity and catalogue.gc == "Program22" and records:
            records[0]["id"] = "P21_01_IDENTITY"
        if unknown_status_gc == catalogue.gc and records:
            records[0]["source_status"] = "알수없음"
        records_by_gc[catalogue.gc] = records
        declared_total = len(records) + (1 if bad_total_gc == catalogue.gc else 0)
        last = max(
            1,
            (declared_total + jeonju.JEONJU_PAGE_SIZE - 1)
            // jeonju.JEONJU_PAGE_SIZE,
        )
        for page in range(1, last + 1):
            start = (page - 1) * jeonju.JEONJU_PAGE_SIZE
            current = records[start : start + jeonju.JEONJU_PAGE_SIZE]
            pages[jeonju.jeonju_list_url(catalogue, page)] = _list_page(
                current,
                catalogue,
                page=page,
                total=declared_total,
            )
        sentinel_url = jeonju.jeonju_list_url(catalogue, last + 1)
        pages[sentinel_url] = (
            _list_page(
                records[:1],
                catalogue,
                page=1,
                total=max(1, declared_total),
            )
            if bad_sentinel_gc == catalogue.gc
            else "<html><body><script>alert('페이지 정보가 올바르지 않습니다.');</script></body></html>"
        )
        for offset, record in enumerate(records):
            detail_page = offset // jeonju.JEONJU_PAGE_SIZE + 1
            pages[
                jeonju.jeonju_detail_url(catalogue, record["id"], detail_page)
            ] = _detail_page(
                record,
                catalogue,
                missing_address=(
                    catalogue.gc == "Program27" or missing_address_gc == catalogue.gc
                ),
                missing_control=missing_control_gc == catalogue.gc,
                auth_suppressed=auth_suppressed_gc == catalogue.gc,
                missing_schedule=missing_schedule_gc == catalogue.gc,
                offhost_control=offhost_control_gc == catalogue.gc,
                bad_title=bad_title_gc == catalogue.gc,
            )

    calls: list[str] = []

    def fetch(_session: Any, url: str, _timeout: int) -> str:
        calls.append(url)
        if url not in pages:
            raise AssertionError(f"unexpected GET {url}")
        return pages[url]

    fetch.calls = calls  # type: ignore[attr-defined]
    make_session, sessions = _factory()
    return fetch, make_session, sessions, records_by_gc


def _collect(fetch: Any, make_session: Any, **kwargs: Any) -> tuple[Any, ...]:
    return jeonju.collect_jeonju_education_courses(
        JeonjuTarget(),
        timeout=7,
        max_pages=100,
        detail_limit=100,
        fetcher=fetch,
        session_factory=make_session,
        dedupe_rows=lambda rows: rows,
        today="2099-07-20",
        **kwargs,
    )


def test_routes_full_menu_and_classifies_non_course_candidates() -> None:
    assert jeonju.JEONJU_CANONICAL_CANDIDATE_ID == "MUNI_IR_4AB296DC50C6"
    assert [item.gc for item in jeonju.JEONJU_CATALOGUES] == [
        "Program21",
        "Program22",
        "Program23",
        "Program24",
        "Program25",
        "Program26",
        "Program27",
    ]
    assert jeonju.is_jeonju_education_target(JeonjuTarget())
    assert jeonju.is_jeonju_education_target(
        JeonjuTarget(url="https://e.jeonju.go.kr/main/menu?gc=Program21")
    )
    assert not jeonju.is_jeonju_education_target(
        JeonjuTarget(url=jeonju.JEONJU_OWNERSHIP_ALIAS_URLS[-1])
    )
    assert all(
        jeonju.is_jeonju_ownership_alias_target({"url": value})
        for value in jeonju.JEONJU_OWNERSHIP_ALIAS_URLS
    )
    assert all(
        jeonju.is_jeonju_excluded_non_course_target({"url": value})
        for value in jeonju.JEONJU_EXCLUDED_NON_COURSE_URLS
    )
    assert set(jeonju.JEONJU_MUNICIPALITY_NAMES) == {
        "5211000000",
        "5211100000",
        "5211300000",
    }

    escaped = BeautifulSoup(
        "<li><div class='tit'><span class='cate'>[인문교양]</span>"
        "전주 역사&amp;amp; 음식문화<span class='program_ptype02'>무료</span>"
        "</div></li>",
        "lxml",
    )
    assert jeonju._card_title(escaped.select_one("li"))[0] == "전주 역사& 음식문화"


def test_complete_fanout_details_filter_branch_and_application_contract() -> None:
    fetch, make_session, sessions, _ = _fixture()
    rows, parser, meta = _collect(fetch, make_session)

    assert parser == jeonju.JEONJU_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["source_total"] == meta["detail_pages"] == 19
    assert meta["current_count"] == len(rows) == 17
    assert meta["expired_count"] == 2
    assert meta["required_list_requests"] == meta["pages"] == 15
    assert meta["declared_totals"]["Program21"] == 13
    assert meta["declared_pages"]["Program21"] == 2
    assert meta["page_counts"]["Program21:1"] == 12
    assert meta["page_counts"]["Program21:2"] == 1
    assert meta["page_counts"]["Program21:3"] == 0
    assert meta["source_status_counts"] == {
        "CLOSED": 16,
        "OPEN": 1,
        "SCHEDULED": 1,
        "WAITING": 1,
    }
    assert meta["municipality_counts"] == {
        "전북특별자치도 전주시": 1,
        "전북특별자치도 전주시 덕진구": 18,
    }
    assert meta["current_municipality_counts"] == {
        "전북특별자치도 전주시 덕진구": 17
    }
    assert meta["municipality_evidence_counts"] == {
        "detail_venue_address": 18,
        "official_operator_no_detail_venue": 1,
    }
    assert meta["application_open_count"] == 2
    assert len(fetch.calls) == 15 + 19
    assert sessions and sessions[0].closed is True

    by_id = {
        row["raw_fields"]["program_id"]: row
        for row in rows
    }
    open_row = by_id["P24_OPEN_ID"]
    wait_row = by_id["P25_WAIT_ID"]
    scheduled = by_id["P26_SCHEDULED_ID"]
    assert open_row["status"] == "OPEN"
    assert open_row["application_type"] == "ONLINE_RESERVATION"
    assert open_row["application_url"].startswith(
        "https://e.jeonju.go.kr/main/menu?gc=Program24&do=sinform"
    )
    assert wait_row["status"] == "WAITING"
    assert wait_row["application_type"] == "WAITLIST_APPLY"
    assert scheduled["status"] == "SCHEDULED"
    assert scheduled["application_url"] == ""
    assert all(row["branch"] == jeonju.JEONJU_BRANCH for row in rows)
    assert all(row["municipality_code"] == jeonju.JEONJU_DEOKJIN_CODE for row in rows)

    serialized = json.dumps(rows, ensure_ascii=False)
    assert "063-281-5269" not in serialized
    assert "테스트강사" not in serialized
    assert "program_viewbox" not in serialized
    assert "detail_pairs" not in serialized


def test_authenticated_application_uses_verified_public_detail_entry() -> None:
    fetch, make_session, _sessions, _ = _fixture(
        missing_control_gc="Program24",
        auth_suppressed_gc="Program24",
    )

    rows, _parser, meta = _collect(fetch, make_session)

    assert meta["snapshot_complete"] is True
    row = next(
        row
        for row in rows
        if row["raw_fields"]["program_id"] == "P24_OPEN_ID"
    )
    assert row["status"] == "OPEN"
    assert row["reservation_available"] is True
    assert row["application_url"] == row["raw_url"]
    assert row["application_type"] == "ONLINE_RESERVATION_LOGIN_REQUIRED"
    assert row["raw_fields"]["application_control_present"] is False
    assert row["raw_fields"]["application_login_gate_verified"] is True
    assert row["raw_fields"]["application_control_contract"] == (
        "auth_suppressed_public_detail_entry"
    )


def test_officially_omitted_schedule_uses_explicit_fallback() -> None:
    fetch, make_session, _sessions, _ = _fixture(
        missing_schedule_gc="Program23",
    )

    rows, _parser, meta = _collect(fetch, make_session)

    assert meta["snapshot_complete"] is True
    row = next(
        row
        for row in rows
        if row["raw_fields"]["program_id"] == "P23_CURRENT_ID"
    )
    assert row["schedule_raw"] == "시간 별도 안내"
    assert row["description"] == row["title"]
    assert row["raw_fields"]["schedule_contract"] == (
        "official_detail_omits_schedule"
    )


def test_managed_session_rotation_and_one_transient_retry(monkeypatch) -> None:
    fetch, make_session, sessions, _ = _fixture()
    transient_url = jeonju.jeonju_detail_url(
        jeonju.JEONJU_CATALOGUE_BY_GC["Program24"],
        "P24_OPEN_ID",
    )
    attempts = 0

    def flaky_fetch(session: Any, url: str, timeout: int) -> str:
        nonlocal attempts
        if url == transient_url and attempts == 0:
            attempts += 1
            raise requests.ConnectionError("transient disconnect")
        return fetch(session, url, timeout)

    monkeypatch.setattr(jeonju, "JEONJU_SESSION_REQUEST_LIMIT", 10)
    rows, _parser, meta = _collect(flaky_fetch, make_session)

    assert rows
    assert meta["snapshot_complete"] is True
    assert meta["request_retry_count"] == 1
    assert meta["sessions_created"] >= 4
    assert len(sessions) == meta["sessions_created"]
    assert all(session.closed for session in sessions)


def test_empty_complete_fanout_is_safe_no_current_snapshot() -> None:
    fetch, make_session, _sessions, _ = _fixture(empty=True)
    rows, _parser, meta = _collect(fetch, make_session)

    assert rows == []
    assert meta["source_total"] == 0
    assert meta["pages"] == meta["required_list_requests"] == 14
    assert meta["snapshot_complete"] is True
    assert meta["details_complete"] is True
    assert meta["no_current_data"] is True
    assert "empty" in meta["no_current_reason"]


def test_caps_fail_closed_before_any_partial_snapshot() -> None:
    fetch, make_session, _sessions, _ = _fixture()
    rows, _parser, meta = jeonju.collect_jeonju_education_courses(
        JeonjuTarget(),
        timeout=7,
        max_pages=14,
        detail_limit=100,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-20",
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert "15 required list requests" in meta["configured_collection_error"]

    fetch, make_session, _sessions, _ = _fixture()
    rows, _parser, meta = jeonju.collect_jeonju_education_courses(
        JeonjuTarget(),
        timeout=7,
        max_pages=100,
        detail_limit=18,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-20",
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["detail_pages"] == 0
    assert "18 of 19 required details" in meta["configured_collection_error"]


def test_pagination_identity_and_detail_contract_fail_closed() -> None:
    cases = [
        ({"bad_sentinel_gc": "Program24"}, "sentinel page is not empty"),
        ({"bad_total_gc": "Program23"}, "expected 2 rows"),
        ({"duplicate_identity": True}, "duplicate program identities"),
        ({"unknown_status_gc": "Program26"}, "malformed catalogue rows"),
        ({"missing_address_gc": "Program23"}, "venue address is missing"),
        ({"missing_control_gc": "Program24"}, "has no application control"),
        ({"offhost_control_gc": "Program24"}, "has no application control"),
        ({"bad_title_gc": "Program25"}, "detail/list title mismatch"),
    ]
    for options, expected in cases:
        fetch, make_session, _sessions, _ = _fixture(**options)
        rows, _parser, meta = _collect(fetch, make_session)
        assert rows == [], options
        assert meta["snapshot_complete"] is False, options
        assert expected in meta["configured_collection_error"], options


def test_dedupe_is_a_validator_and_never_authorizes_partial_save() -> None:
    fetch, make_session, _sessions, _ = _fixture()
    rows, _parser, meta = jeonju.collect_jeonju_education_courses(
        JeonjuTarget(),
        timeout=7,
        max_pages=100,
        detail_limit=100,
        fetcher=fetch,
        session_factory=make_session,
        dedupe_rows=lambda values: values[:-1],
        today="2099-07-20",
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "dedupe changed complete row count" in meta["configured_collection_error"]
