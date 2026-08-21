from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest

from Crawler import municipal_suwon_reservation as suwon


@dataclass(frozen=True)
class Target:
    provider: str
    url: str
    branch: str = "수원시 통합예약"


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


BRANCHES = (
    "공원녹지사업소",
    "수원시평생학습관",
    "수원여성인력개발센터",
    "수원YWCA",
    "영통구청",
    "수원시",
    "영흥수목원",
    "일월수목원",
    "수원화성박물관",
    "장안구청",
    "팔달구청",
    "수원시 환경성질환 아토피센터",
)


def _target() -> Target:
    return Target(suwon.SUWON_PROVIDER, suwon.SUWON_URL)


def _items() -> dict[str, list[dict[str, Any]]]:
    scopes: dict[str, list[dict[str, Any]]] = {"72": [], "73": []}
    global_index = 0
    for index in range(130):
        if index < 41:
            status = "접수중"
            kind = "seqNo"
        elif index < 44:
            status = "대기접수"
            kind = "seqNo"
        elif index < 49:
            status = "접수마감"
            kind = "seqNo"
        else:
            status = "접수중"
            kind = "eduMstSeq"
        expired = index in {126, 127}
        scopes["72"].append(
            {
                "status_code": "72",
                "number": 130 - index,
                "kind": kind,
                "identity": str(12000 + index) if kind == "seqNo" else str(20260719000000 + index),
                "title": f"수원 통합교육 72-{130 - index}",
                "status": status,
                "apply_start": "2026-01-01" if expired else "2026-07-01",
                "apply_end": "2026-01-31" if expired else "2026-07-31",
                "start": "2026-02-01" if expired else "2026-07-19",
                "end": "2026-03-31" if expired else "2026-08-31",
                "branch": BRANCHES[global_index % len(BRANCHES)],
                "venue": f"교육장 {global_index % 17}",
                "target": "수원시민",
                "global_index": global_index,
            }
        )
        global_index += 1
    for index in range(54):
        scheduled = index < 42
        kind = "seqNo" if scheduled else "eduMstSeq"
        scopes["73"].append(
            {
                "status_code": "73",
                "number": 54 - index,
                "kind": kind,
                "identity": str(13000 + index) if kind == "seqNo" else str(20260720000000 + index),
                "title": f"수원 통합교육 73-{54 - index}",
                "status": "접수준비" if scheduled else "접수마감",
                "apply_start": "2026-08-01" if scheduled else "2026-12-18",
                "apply_end": "2026-08-15" if scheduled else "2025-12-24",
                "start": "2026-09-01" if scheduled else "2026-01-01",
                "end": "2026-10-31" if scheduled else "2026-03-31",
                "branch": BRANCHES[global_index % len(BRANCHES)],
                "venue": f"교육장 {global_index % 17}",
                "target": "수원시민",
                "global_index": global_index,
            }
        )
        global_index += 1
    scopes["72"][0]["official_category_code"] = (
        suwon.SUWON_EXPERIENCE_CATEGORY_CODE
    )
    scopes["73"][0]["official_category_code"] = (
        suwon.SUWON_EXPERIENCE_CATEGORY_CODE
    )
    return scopes


def _list_row(item: dict[str, Any]) -> str:
    category_query = (
        f"q_categoryCode={item['category_code']}&amp;"
        if item.get("category_code")
        else ""
    )
    href = (
        f"/web/reserv/edu/view.do?{item['kind']}={item['identity']}&amp;"
        f"q_progressStatusCd={item['status_code']}&amp;q_rowPerPage=100&amp;"
        f"{category_query}q_currPage=1&amp;"
    )
    return f"""
      <tr>
        <td>{item['number']}</td>
        <td><a class="title" href="{href}">{item['title']}</a></td>
        <td>{item['apply_start']} ~ {item['apply_end']}<br>
            {item['start']} ~ {item['end']}</td>
        <td>월<br>10:00 ~ 12:00</td><td>{item['target']}</td>
        <td>3 / 20<br>(1 / 5)</td><td>{item['venue']}</td><td>{item['status']}</td>
      </tr>
    """


def _list_page(items: list[dict[str, Any]]) -> str:
    return "<html><body><table><tbody>" + "".join(_list_row(item) for item in items) + "</tbody></table></body></html>"


def _detail_page(
    item: dict[str, Any],
    *,
    missing_branch: bool = False,
    bad_control: bool = False,
) -> str:
    method = "" if item["global_index"] < 6 else "인터넷"
    branch = "" if missing_branch else item["branch"]
    venue_index = item["global_index"] % 17
    district = ("팔달구", "장안구", "권선구", "영통구")[venue_index % 4]
    venue_address = f"경기도 수원시 {district} 테스트로 {100 + venue_index}"
    venue_lat = 37.20 + venue_index / 1000
    venue_lon = 127.00 + venue_index / 1000
    map_script = (
        f"""
          <script>
            var latitude = {venue_lat},
                longitude = {venue_lon};
            var content = '<em>주소 : </em><span class="text">{venue_address}{item['venue']}</span>';
          </script>
        """
        if item["global_index"] < 17 or item["global_index"] % 4
        else ""
    )
    control = ""
    if item["kind"] == "seqNo" and item["status"] in {"접수중", "대기접수"}:
        identity = "999999" if bad_control else item["identity"]
        status_type = "SB" if item["status"] == "대기접수" else "AA"
        control = f"<a href=\"#none\" onclick=\"jsForm('{identity}', '{status_type}');\">신청</a>"
    return f"""
      <html><body><span class="title">{item['title']}</span>
      <table><tbody>
        <tr><th>접수방법</th><td>{method}</td><th>교육일정</th><td>{item['start']} ~ {item['end']}</td></tr>
        <tr><th>대상성별</th><td>전체</td><th>접수기간</th><td>{item['apply_start']} ~ {item['apply_end']}</td></tr>
        <tr><th>교육대상</th><td>{item['target']}</td><th>교육요일</th><td>월</td></tr>
        <tr><th>모집인원</th><td>20명</td><th>교육시간</th><td>10:00 ~ 12:00</td></tr>
        <tr><th>비용</th><td>무료</td><th>교육기관</th><td>{branch}</td></tr>
        <tr><th>문의처</th><td>031-000-0000</td><th>교육장소</th><td>
          <span class="location_text">{item['venue']}</span>
          <a class="location_view">위치보기</a>
          {map_script}
        </td></tr>
        <tr><th>교육내용</th><td>교육 상세 내용</td></tr>
      </tbody></table>{control}</body></html>
    """


def _fixture(
    *,
    malformed_numbering: bool = False,
    missing_branch_identity: str = "",
    bad_control_identity: str = "",
    current_invalid_apply: bool = False,
):
    scopes = _items()
    if malformed_numbering:
        scopes["72"][100]["number"] = 29
    if current_invalid_apply:
        scopes["72"][0]["apply_start"] = "2026-08-01"
        scopes["72"][0]["apply_end"] = "2026-07-01"

    mapping: dict[str, str] = {}
    mapping[suwon.suwon_list_url("72", 1)] = _list_page(scopes["72"][:100])
    mapping[suwon.suwon_list_url("72", 2)] = _list_page(scopes["72"][100:])
    mapping[suwon.suwon_list_url("73", 1)] = _list_page(scopes["73"])
    for status_code in suwon.SUWON_STATUS_SCOPES:
        category_items = [
            {
                **item,
                "number": number,
                "category_code": suwon.SUWON_EXPERIENCE_CATEGORY_CODE,
            }
            for number, item in zip(
                range(
                    sum(
                        row.get("official_category_code")
                        == suwon.SUWON_EXPERIENCE_CATEGORY_CODE
                        for row in scopes[status_code]
                    ),
                    0,
                    -1,
                ),
                (
                    row
                    for row in scopes[status_code]
                    if row.get("official_category_code")
                    == suwon.SUWON_EXPERIENCE_CATEGORY_CODE
                ),
            )
        ]
        mapping[suwon.suwon_category_list_url(status_code, 1)] = _list_page(
            category_items
        )
    for item in scopes["72"] + scopes["73"]:
        if item["end"] < "2026-07-19":
            continue
        mapping[suwon.suwon_detail_url(item["kind"], item["identity"])] = _detail_page(
            item,
            missing_branch=item["identity"] == missing_branch_identity,
            bad_control=item["identity"] == bad_control_identity,
        )

    calls: list[str] = []
    sessions: list[DummySession] = []

    def fetch(_session: Any, url: str, _timeout: int) -> str:
        calls.append(url)
        return mapping[url]

    def make_session() -> DummySession:
        current = DummySession()
        sessions.append(current)
        return current

    return scopes, mapping, fetch, make_session, calls, sessions


def test_exact_route_and_safe_url_builders() -> None:
    assert suwon.is_suwon_reservation_target(_target())
    assert not suwon.is_suwon_reservation_target(Target("OTHER", suwon.SUWON_URL))
    assert not suwon.is_suwon_reservation_target(
        Target(suwon.SUWON_PROVIDER, suwon.SUWON_URL + "?q_currPage=1")
    )
    assert suwon.suwon_list_url("72", 2).endswith(
        "q_rowPerPage=100&q_progressStatusCd=72&q_currPage=2"
    )
    assert suwon.suwon_list_url("99", 1) == ""
    assert suwon.suwon_category_list_url("72", 2).endswith(
        "q_rowPerPage=100&q_progressStatusCd=72&q_categoryCode=81&q_currPage=2"
    )
    assert suwon.suwon_category_list_url("72", 1, "80") == ""
    assert suwon.suwon_detail_url("seqNo", "11890").endswith("seqNo=11890")
    assert suwon.suwon_detail_url("eduMstSeq", "20260416104111").endswith(
        "eduMstSeq=20260416104111"
    )
    assert suwon.suwon_detail_url("seqNo", "11890&admin=1") == ""
    assert suwon.suwon_application_url("11890").endswith(
        "seqNo=11890&statusType=AA"
    )
    assert suwon.suwon_application_url("11890", "SB").endswith(
        "seqNo=11890&statusType=SB"
    )
    assert suwon.suwon_application_url("11890", "ADMIN") == ""


def test_live_shaped_snapshot_returns_all_170_current_details() -> None:
    scopes, _mapping, fetch, make_session, calls, sessions = _fixture()

    rows, parser, meta = suwon.collect_suwon_reservation_education(
        _target(),
        fetcher=fetch,
        session_factory=make_session,
        today=date(2026, 7, 19),
        max_pages=5,
        detail_limit=170,
        max_workers=1,
    )

    assert parser == suwon.SUWON_PARSER
    assert len(rows) == 170
    assert len({row["provider_course_id"] for row in rows}) == 170
    assert {row["raw_fields"]["identity_kind"] for row in rows} == {
        "seqNo",
        "eduMstSeq",
    }
    assert {row["branch"] for row in rows} == {
        f"교육장 {index}" for index in range(17)
    }
    assert {row["provider_organizer"] for row in rows} == set(BRANCHES)
    assert Counter(row["municipality_code"] for row in rows) == {
        "4111100000": 41,
        "4111300000": 40,
        "4111500000": 50,
        "4111700000": 39,
    }
    assert all(row["municipality_region_verified"] is True for row in rows)
    experience_rows = [
        row for row in rows if row["domain_category"] == "체험·견학"
    ]
    education_rows = [
        row for row in rows if row["domain_category"] == "교육·강좌"
    ]
    assert len(experience_rows) == 2
    assert len(education_rows) == 168
    assert all(row["service_group"] == "체험" for row in experience_rows)
    assert all(row["program_type"] == "체험" for row in experience_rows)
    assert all(row["category"] == "답사·체험" for row in experience_rows)
    assert all(row["service_group"] == "공공강좌" for row in education_rows)
    assert all(
        row["raw_fields"]["official_experience_category"]["matched"] is False
        for row in education_rows
    )
    assert all(
        row["raw_fields"]["official_experience_category"]
        ["canonical_row_verified"]
        is True
        and row["raw_fields"]["official_experience_category"]
        ["detail_title_verified"]
        is True
        for row in experience_rows
    )
    assert {row["provider"] for row in experience_rows} == {suwon.SUWON_PROVIDER}
    assert {
        row["provider_course_id"] for row in experience_rows
    } == {
        f"{suwon.SUWON_PROVIDER}:seq:{scopes['72'][0]['identity']}",
        f"{suwon.SUWON_PROVIDER}:seq:{scopes['73'][0]['identity']}",
    }
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["branch"] == row["venue_name"] for row in rows)
    assert all(row["address"] == row["venue_address"] for row in rows)
    assert all(row["branch_address"] == row["venue_address"] for row in rows)
    assert all(row["branch_location_verified"] is True for row in rows)
    assert all(
        row["branch_coordinate_source"] == "SUWON_OFFICIAL_DETAIL_MAP"
        for row in rows
    )
    assert any(
        row["raw_fields"].get("official_location_inherited") is True
        for row in rows
    )
    first = rows[0]
    assert first["venue_address"] == "경기도 수원시 팔달구 테스트로 100"
    assert first["branch_lat"] == pytest.approx(37.20)
    assert first["branch_lon"] == pytest.approx(127.00)
    assert first["basic_info"] == {
        "location_role": "course_venue",
        "education_institution": "공원녹지사업소",
    }
    assert sum(bool(row.get("application_url")) for row in rows) == 44
    assert sum(row["reservation_available"] for row in rows) == 44
    assert all(
        row["application_url"].startswith(
            "https://www.suwon.go.kr/web/reserv/edu/reservForm.do?seqNo="
        )
        for row in rows
        if row.get("application_url")
    )
    assert meta == {
        **meta,
        "pages": 5,
        "required_list_requests": 5,
        "source_total": 184,
        "source_totals": {"72": 130, "73": 54},
        "source_pages": {"72": 2, "73": 1},
        "identity_kind_counts": {"seqNo": 91, "eduMstSeq": 93},
        "expired_count": 14,
        "historical_invalid_apply_period_count": 12,
        "current_invalid_apply_period_count": 0,
        "current_count": 170,
        "experience_current_count": 2,
        "education_current_count": 168,
        "domain_category_counts": {"체험·견학": 2, "교육·강좌": 168},
        "experience_source_total": 2,
        "experience_source_totals": {"72": 1, "73": 1},
        "experience_source_pages": {"72": 1, "73": 1},
        "experience_category_complete": True,
        "returned_count": 170,
        "required_detail_count": 170,
        "detail_attempts": 170,
        "detail_pages": 170,
        "status_counts": {
            "OPEN": 120,
            "WAITLIST": 3,
            "CLOSED": 5,
            "SCHEDULED": 42,
        },
        "branch_count": 17,
        "municipality_counts": {
            "4111100000": 41,
            "4111300000": 40,
            "4111500000": 50,
            "4111700000": 39,
        },
        "municipality_resolution_counts": {
            "official_detail_map_address": 170,
        },
        "parent_municipality_fallback_count": 0,
        "reservation_discovery_links": 44,
        "pagination_complete": True,
        "details_complete": True,
        "snapshot_complete": True,
        "source_cap_reached": False,
    }
    assert calls[:5] == [
        suwon.suwon_list_url("72", 1),
        suwon.suwon_list_url("73", 1),
        suwon.suwon_list_url("72", 2),
        suwon.suwon_category_list_url("72", 1),
        suwon.suwon_category_list_url("73", 1),
    ]
    assert len(calls) == 5 + 170
    assert all(session.closed for session in sessions)
    assert len(scopes["72"]) + len(scopes["73"]) == 184


@pytest.mark.parametrize(
    (
        "venue_address",
        "venue_name",
        "institution",
        "expected_code",
        "expected_full_name",
        "expected_sigungu",
        "expected_kind",
    ),
    [
        (
            "경기도 수원시 장안구 정조로 1085",
            "수원시 목공체험장",
            "공원녹지사업소",
            "4111100000",
            "경기도 수원시 장안구",
            "수원시 장안구",
            "official_detail_map_address",
        ),
        (
            "경기도 수원시 권선구 서호로 16",
            "서울대학교 수원수목원",
            "공원녹지사업소",
            "4111300000",
            "경기도 수원시 권선구",
            "수원시 권선구",
            "official_detail_map_address",
        ),
        (
            "경기도 수원시 팔달구 향교로 130",
            "부국원 3층 교육실",
            "수원시",
            "4111500000",
            "경기도 수원시 팔달구",
            "수원시 팔달구",
            "official_detail_map_address",
        ),
        (
            "수원시 영통구 영통로 435",
            "영흥수목원",
            "영흥수목원",
            "4111700000",
            "경기도 수원시 영통구",
            "수원시 영통구",
            "official_detail_map_address",
        ),
        (
            "",
            "수원여성인력개발센터",
            "수원여성인력개발센터",
            "4111700000",
            "경기도 수원시 영통구",
            "수원시 영통구",
            "official_exact_venue_institution_registry",
        ),
        (
            "",
            "새 교육장",
            "수원시",
            "4111000000",
            "경기도 수원시",
            "수원시",
            "conservative_parent_no_exact_district_evidence",
        ),
        (
            "수원시 장안구·권선구 순회",
            "순회 교육장",
            "수원시",
            "4111000000",
            "경기도 수원시",
            "수원시",
            "conservative_parent_ambiguous_address",
        ),
    ],
)
def test_collector_row_municipality_survives_target_metadata_and_branch_writer(
    venue_address: str,
    venue_name: str,
    institution: str,
    expected_code: str,
    expected_full_name: str,
    expected_sigungu: str,
    expected_kind: str,
) -> None:
    from Crawler import Crawler_GeneratedYamlTargets as generated_targets
    from Crawler import Crawler_MunicipalYaml as municipal

    row = {
        "title": "공식 수원 교육",
        "branch": venue_name,
        "branch_code": "SUWON_TEST_BRANCH",
        "venue_name": venue_name,
        "venue_address": venue_address,
        "provider_organizer": institution,
        "raw_fields": {},
    }
    suwon._assign_suwon_municipality(row)
    target = municipal.CrawlTarget(
        provider=suwon.SUWON_PROVIDER,
        name="수원시 통합예약 전체 교육",
        branch="수원시 통합예약",
        url=suwon.SUWON_URL,
        source="test",
        region="경기도 수원시",
        extra={
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "municipality_code": suwon.SUWON_MUNICIPALITY_CODE,
            "municipality_full_name": suwon.SUWON_MUNICIPALITY_NAME,
            "covered_municipalities": [
                {
                    "code": "4111000000",
                    "sido": "경기도",
                    "sigungu": "수원시",
                    "full_name": "경기도 수원시",
                },
                *[
                    {
                        "code": code,
                        "sido": "경기도",
                        "sigungu": full_name.removeprefix("경기도 "),
                        "full_name": full_name,
                    }
                    for code, full_name in suwon.SUWON_DISTRICT_MUNICIPALITIES.values()
                ],
            ],
        },
    )

    generated_targets.apply_target_metadata([row], target)
    branch = municipal.MunicipalDbWriter(suwon.SUWON_PROVIDER).branch_info_from_row(
        row
    )

    assert row["municipality_code"] == expected_code
    assert row["municipality_full_name"] == expected_full_name
    assert row["municipality_region_verified"] is True
    assert row["raw_fields"]["municipality_resolution"]["kind"] == expected_kind
    assert branch["region_sido"] == "경기도"
    assert branch["region_sigungu"] == expected_sigungu


def test_operational_target_covers_parent_suwon_and_all_four_districts() -> None:
    from Crawler import Crawler_MunicipalIntegratedReservation as integrated

    targets = integrated.load_municipal_targets(scheduled_providers=set())
    target = next(
        row
        for row in targets
        if row["provider"] == suwon.SUWON_PROVIDER
        and row["url"] == suwon.SUWON_URL
    )

    assert [row["code"] for row in target["covered_municipalities"]] == [
        "4111000000",
        "4111100000",
        "4111300000",
        "4111500000",
        "4111700000",
    ]


def test_list_page_cap_is_fail_closed_before_partial_pagination() -> None:
    _scopes, _mapping, fetch, make_session, calls, _sessions = _fixture()

    rows, _parser, meta = suwon.collect_suwon_reservation_education(
        _target(),
        fetcher=fetch,
        session_factory=make_session,
        today=date(2026, 7, 19),
        max_pages=2,
        detail_limit=300,
    )

    assert rows == []
    assert calls == [suwon.suwon_list_url("72", 1), suwon.suwon_list_url("73", 1)]
    assert meta["source_cap_reached"] is True
    assert meta["pagination_complete"] is False
    assert meta["snapshot_complete"] is False
    assert "2 of 3 required list requests" in meta["configured_collection_error"]


def test_source_number_repetition_is_fail_closed() -> None:
    _scopes, _mapping, fetch, make_session, _calls, _sessions = _fixture(
        malformed_numbering=True
    )

    rows, _parser, meta = suwon.collect_suwon_reservation_education(
        _target(),
        fetcher=fetch,
        session_factory=make_session,
        today=date(2026, 7, 19),
        max_pages=3,
        detail_limit=300,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "source numbering mismatch" in meta["configured_collection_error"]


def test_experience_category_row_must_match_the_canonical_provider_ledger() -> None:
    scopes, mapping, fetch, make_session, calls, _sessions = _fixture()
    category_url = suwon.suwon_category_list_url("72", 1)
    category_title = scopes["72"][0]["title"]
    mapping[category_url] = mapping[category_url].replace(
        category_title,
        f"{category_title} 변조",
        1,
    )

    rows, _parser, meta = suwon.collect_suwon_reservation_education(
        _target(),
        fetcher=fetch,
        session_factory=make_session,
        today=date(2026, 7, 19),
        max_pages=5,
        detail_limit=170,
        max_workers=1,
    )

    assert rows == []
    assert len(calls) == 5
    assert meta["experience_category_complete"] is False
    assert meta["detail_attempts"] == 0
    assert "category 81" in meta["configured_collection_error"]
    assert "row mismatch" in meta["configured_collection_error"]


@pytest.mark.parametrize("failure", ("missing_branch", "bad_control"))
def test_required_detail_contract_is_fail_closed(failure: str) -> None:
    source = _items()["72"][0]
    kwargs = (
        {"missing_branch_identity": source["identity"]}
        if failure == "missing_branch"
        else {"bad_control_identity": source["identity"]}
    )
    _scopes, _mapping, fetch, make_session, _calls, _sessions = _fixture(**kwargs)

    rows, _parser, meta = suwon.collect_suwon_reservation_education(
        _target(),
        fetcher=fetch,
        session_factory=make_session,
        today=date(2026, 7, 19),
        max_pages=5,
        detail_limit=170,
        max_workers=1,
    )

    assert rows == []
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert (
        "empty education institution" in meta["configured_collection_error"]
        if failure == "missing_branch"
        else "application control" in meta["configured_collection_error"]
    )


def test_reversed_current_application_period_is_not_tolerated() -> None:
    _scopes, _mapping, fetch, make_session, calls, _sessions = _fixture(
        current_invalid_apply=True
    )

    rows, _parser, meta = suwon.collect_suwon_reservation_education(
        _target(),
        fetcher=fetch,
        session_factory=make_session,
        today=date(2026, 7, 19),
        max_pages=5,
        detail_limit=300,
    )

    assert rows == []
    assert len(calls) == 5
    assert meta["current_invalid_apply_period_count"] == 1
    assert meta["historical_invalid_apply_period_count"] == 12
    assert meta["snapshot_complete"] is False
    assert "current application period is reversed" in meta["configured_collection_error"]


def test_shared_router_uses_exact_suwon_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from Crawler import Crawler_MunicipalYaml as municipal

    sentinel = ([{"title": "수원"}], "suwon-parser", {"snapshot_complete": True})
    monkeypatch.setattr(
        suwon,
        "collect_suwon_reservation_education",
        lambda *_args, **_kwargs: sentinel,
    )
    target = municipal.CrawlTarget(
        provider=suwon.SUWON_PROVIDER,
        name="수원시 통합예약 교육",
        branch="수원시 통합예약",
        url=suwon.SUWON_URL,
        source="test",
    )

    assert municipal.collect_from_url(
        target,
        timeout=1,
        max_depth=0,
        max_pages=3,
        detail_limit=170,
    ) == sentinel
