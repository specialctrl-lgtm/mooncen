from __future__ import annotations

import hashlib
import os
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_gunpo as gunpo
from utils.outbound_http import SafeSession


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.history = []
        self.content = b"{}"

    def json(self):
        return self._payload


class FakeSession:
    def close(self):
        return None


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def picturebook_list_payload(page: int, *, title: str = "여름 그림책 교실"):
    rows = []
    if page == 1:
        rows = [
            {
                "eduProgrmNo": 77,
                "eduProgrmNm": title,
                "eduGroupCdNm": "교육",
                "eduBgngYmd": "20260801",
                "eduEndYmd": "20260802",
                "rcptBgngPnttm": "2026-07-20",
                "rcptEndPnttm": "2026-07-30",
                "eduPlace": "그림책꿈마루 교육실",
                "eduTrgt": "초등학생",
                "tutfee": 0,
                "mxmmAplyNope": 12,
                "rcptTyCdNm": "선착순",
            }
        ]
    return {
        "total": 1,
        "pagination": {"currentPageNo": page, "totalRecordCount": 1},
        "list": rows,
    }


def picturebook_detail_payload(*, title: str = "여름 그림책 교실"):
    return {
        "eduProgrmNo": 77,
        "eduProgrmNm": title,
        "eduBgngYmd": "20260801",
        "eduEndYmd": "20260802",
        "eduPlace": "그림책꿈마루 교육실",
        "mxmmAplyNope": 12,
        "crseFyerSchdulList": [{"weekCdNm": "토"}],
        "eduIntrcnCn": "담당자 010-1234-5678",
        "crclmCn": "저장하지 않는 자유서술",
        "requestForm": {"phone": True},
    }


def picturebook_fetcher(*, tamper_detail: bool = False):
    calls: list[str] = []

    def fetch(_session, url: str, _timeout: int):
        calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path.endswith("/api/edu/progrm/list"):
            return FakeResponse(picturebook_list_payload(int(query["pageIndex"][0])))
        if parsed.path.endswith("/api/edu/progrm/view"):
            title = "다른 강좌" if tamper_detail else "여름 그림책 교실"
            return FakeResponse(picturebook_detail_payload(title=title))
        raise AssertionError(f"unexpected request: {url}")

    return calls, fetch


def test_exact_owner_targets_and_new_provider_hashes():
    assert set(gunpo.GUNPO_OWNERS) == {
        "city",
        "info",
        "foundation",
        "media",
        "library",
        "urban",
        "youth",
        "picturebook",
        "flying",
    }
    for owner, config in gunpo.GUNPO_OWNERS.items():
        assert gunpo.owner_for_target(config) == owner
        assert gunpo.is_target(config)
        assert not gunpo.is_target(
            {"provider": config["provider"], "url": config["url"] + "&alias=1"}
        )
    for owner in (
        "foundation",
        "media",
        "library",
        "urban",
        "youth",
        "picturebook",
        "flying",
    ):
        config = gunpo.GUNPO_OWNERS[owner]
        expected = hashlib.sha1(config["url"].encode("utf-8")).hexdigest()[:8].upper()
        assert config["provider"].endswith("_" + expected)
    assert gunpo.owner_for_target(
        {
            "provider": "MUNI_WWW_GUNPO_GO_KR_09F1E7BC",
            "url": "https://www.gunpo.go.kr/portal/index.do",
        }
    ) == ""


def test_only_public_get_list_detail_and_registry_routes_are_allowlisted():
    safe = [
        ("city", gunpo.city_list_url(1), "list"),
        ("city", gunpo.city_detail_url("1"), "detail"),
        ("info", gunpo.info_list_url(1), "list"),
        ("info", gunpo.info_detail_url("1"), "detail"),
        ("foundation", gunpo.foundation_list_url("21200003", 1), "list"),
        (
            "foundation",
            gunpo.foundation_detail_url(("100003", "", "21200003", "0018", "0001", "20090023")),
            "detail",
        ),
        ("media", gunpo.media_list_url(1), "list"),
        ("media", gunpo.media_detail_url("1", 1), "detail"),
        ("library", gunpo.library_list_url(1, 0), "list"),
        ("library", gunpo.library_detail_url(1), "detail"),
        ("urban", gunpo.fmcs_company_url("urban"), "registry"),
        ("urban", gunpo.fmcs_list_url("urban", "R", 1), "list"),
        ("urban", gunpo.fmcs_detail_url("urban", "GUNPO03", "00001"), "detail"),
        ("youth", gunpo.fmcs_company_url("youth"), "registry"),
        ("youth", gunpo.fmcs_category_url(), "registry"),
        (
            "youth",
            gunpo.fmcs_list_url("youth", "R", 1, "1020000000"),
            "list",
        ),
        (
            "youth",
            gunpo.fmcs_detail_url("youth", "GUNPOYF01", "00001"),
            "detail",
        ),
        ("picturebook", gunpo.picturebook_list_url(1), "list"),
        ("picturebook", gunpo.picturebook_detail_url(1), "detail"),
        ("flying", gunpo.flying_list_url(1), "list"),
        ("flying", gunpo.flying_detail_url(1), "detail"),
    ]
    for owner, url, expected in safe:
        assert gunpo._classify_url(owner, url) == expected

    forbidden = [
        (
            "city",
            "https://ctm.gunpo.go.kr/portal/webEdcLctreAgree.do?key=1008274&searchLctreKey=1",
        ),
        (
            "library",
            "https://www.gunpolib.go.kr/pyxis-api/1/library-program-requests",
        ),
        (
            "urban",
            "https://www.gunpouc.or.kr/fmcs/155?action=write&comcd=GUNPO03&classcd=1&type=R",
        ),
        (
            "youth",
            "https://www.gpyf.or.kr/yeyak/rest/lecture/family?company_code=GUNPOYF01",
        ),
        ("foundation", "https://www.gunpocf.or.kr/cmm/fms/FileDown.do?file=1"),
        ("flying", "https://docs.google.com/forms/d/e/application/viewform"),
    ]
    for owner, url in forbidden:
        with pytest.raises(gunpo.GunpoContractError):
            gunpo._classify_url(owner, url)


def test_city_priority_window_uses_final_application_and_education_pairs():
    parsed = gunpo._parse_city_page(
        soup(
            """
            <div id="contents">총 1건
              <table><tbody><tr>
                <td>모집마감 교육 중</td>
                <td><a href="/portal/edcLctreView.do?key=1008274&amp;searchLctreKey=6553">노래교실</a></td>
                <td>대야동주민자치회</td><td>성인</td>
                <td>우선 : 2026.06.15~2026.06.17 신청 : 2026.06.25~2026.06.30
                    교육 : 2026.07.01~2026.09.30 강의시간 : (월) 10:00~11:30</td>
                <td>40</td><td>선착순 54,000</td><td>방문</td>
              </tr></tbody></table>
            </div>
            """
        ),
        1,
    )
    row = parsed["rows"][0]
    assert row["apply_start"].isoformat() == "2026-06-25"
    assert row["apply_end"].isoformat() == "2026-06-30"
    assert row["start"].isoformat() == "2026-07-01"
    assert row["end"].isoformat() == "2026-09-30"
    assert gunpo._normalize_city_branch(row["organizer"], "") == "대야동 주민자치회"


def test_foundation_empty_lecture_identity_and_historical_fmcs_contracts():
    foundation = gunpo._parse_foundation_page(
        soup(
            """
            <div id="contents"><table><tbody><tr>
              <td>생활문화아카데미</td>
              <td><a onclick="fnGoViewPage('100003','','21200003','0018', '0001', '20090023');">요가</a></td>
              <td>00.01.01 ~ 00.01.01</td><td>44,000원</td><td>30명</td>
              <td>마감 / 마감</td><td>접수마감</td>
            </tr></tbody></table></div>
            """
        ),
        "21200003",
        1,
    )
    assert foundation["rows"][0]["identity"][1] == ""

    ended = gunpo._fmcs_page(
        [
            {
                "comcd": "GUNPO05",
                "comnm": "군포도시공사 부곡체육시설",
                "class_cd": "00006",
                "class_nm": "새벽수영",
                "status": "E",
                "total_count": 1,
                "category1": "수영",
                "category2": "성인",
                "train_day_nm": "월수금",
                "train_stime": "06:00",
                "train_etime": "06:50",
                "target_age_name": "성인",
                "course_fee": "50,000",
                "capa": 20,
                "reg_person": 20,
            }
        ],
        "urban",
        "E",
        1,
    )
    assert ended["rows"][0]["branch"] == "군포도시공사 부곡체육시설"

    waiting_payload = [
        {
            "comcd": "GUNPOYF01",
            "comnm": "군포시청소년수련관",
            "class_cd": "01359",
            "class_nm": "월수금06교정중C",
            "status": "W",
            "total_count": 1,
            "category1": "수영사업",
            "category2": "새벽수영",
            "train_day_nm": "월수금",
            "train_stime": "06:00",
            "train_etime": "06:50",
            "target_age_name": "청소년",
            "course_fee": "50,000",
            "capa": 20,
            "reg_person": 0,
        }
    ]
    waiting = gunpo._fmcs_page(
        waiting_payload, "youth", "E", 1, "1000000000"
    )
    assert waiting["rows"][0]["raw_status"] == "W"
    ended_in_recruiting_partition = gunpo._fmcs_page(
        [{**waiting_payload[0], "status": "E"}],
        "youth",
        "R",
        1,
        "1000000000",
    )
    assert ended_in_recruiting_partition["rows"][0]["partition"] == "R"
    assert not gunpo._fmcs_is_current(ended_in_recruiting_partition["rows"][0])
    assert gunpo._fmcs_page([], "youth", "E", 2, "1000000000")["rows"] == []


def test_library_collapses_only_identical_source_duplicates():
    row = {
        "identity": 1447,
        "title": "가족이 함께하는 동화한마당",
        "branch": "산본도서관",
    }
    unique, duplicate_count = gunpo._collapse_identical_identity_duplicates(
        [row, dict(row)], "library type 1"
    )
    assert unique == [row]
    assert duplicate_count == 1

    conflicting = {**row, "branch": "중앙도서관"}
    with pytest.raises(gunpo.GunpoContractError, match="conflicting duplicate identity 1447"):
        gunpo._collapse_identical_identity_duplicates(
            [row, conflicting], "library type 1"
        )


def test_media_managed_session_keeps_tls_verification_and_dns_pinning_adapter():
    session = gunpo._media_managed_session(SafeSession)
    try:
        assert session.verify is True
        assert isinstance(
            session.get_adapter("https://www.gpmedia.or.kr/"),
            gunpo._GunpoMediaPinnedAdapter,
        )
    finally:
        session.close()


def test_picturebook_collection_is_complete_detailed_and_pii_free():
    calls, fetch = picturebook_fetcher()
    rows, parser, meta = gunpo.collect_gunpo_education_courses(
        gunpo.GUNPO_OWNERS["picturebook"],
        today="2026-07-23",
        fetcher=fetch,
        session_factory=FakeSession,
    )
    assert parser == gunpo.GUNPO_PARSER
    assert len(rows) == 1
    row = rows[0]
    assert row["branch"] == "그림책꿈마루"
    assert row["collection_category"] == "공공예약"
    assert row["domain_category"] == "교육·강좌"
    assert row["source_group"] == "municipal_reservation"
    assert row["service_group_policy"] == "locked"
    assert row["source_url"] == gunpo.picturebook_detail_url(77)
    assert row["application_url"] == ""
    assert not (set(row) & gunpo._FORBIDDEN_OUTPUT_KEYS)
    assert "010-1234-5678" not in repr(row)
    assert calls.count(gunpo.picturebook_list_url(1)) == 2
    assert calls.count(gunpo.picturebook_list_url(2)) == 2
    assert calls.count(gunpo.picturebook_detail_url(77)) == 1
    assert all(gunpo._classify_url("picturebook", url) in {"list", "detail"} for url in calls)
    assert meta["source_total_count"] == 1
    assert meta["current_source_count"] == 1
    assert meta["sensitive_fields_discarded"] == 3
    assert meta["list_requests"] == 4
    assert meta["detail_requests"] == 1
    assert meta["application_requests"] == meta["post_requests"] == 0
    assert meta["pagination_complete"]
    assert meta["sentinel_verified"]
    assert meta["boundary_recheck_verified"]
    assert meta["details_complete"]
    assert meta["pii_safe"]
    assert meta["snapshot_complete"]
    assert meta["full_snapshot_validated"]
    assert meta["discovered_links"] == 1
    assert meta["no_current_data"] is False


def test_caps_detail_drift_and_dedupe_changes_fail_atomically():
    calls, fetch = picturebook_fetcher()
    rows, _parser, meta = gunpo.collect(
        gunpo.GUNPO_OWNERS["picturebook"],
        today="2026-07-23",
        max_pages=3,
        fetcher=fetch,
        session_factory=FakeSession,
    )
    assert rows == []
    assert len(calls) == 1
    assert meta["source_cap_reached"]
    assert not meta["snapshot_complete"]

    _calls, tampered = picturebook_fetcher(tamper_detail=True)
    rows, _parser, meta = gunpo.collect(
        gunpo.GUNPO_OWNERS["picturebook"],
        today="2026-07-23",
        fetcher=tampered,
        session_factory=FakeSession,
    )
    assert rows == []
    assert "identity drift" in meta["configured_collection_error"]
    assert meta["returned_count"] == 0

    _calls, fetch = picturebook_fetcher()
    rows, _parser, meta = gunpo.collect(
        gunpo.GUNPO_OWNERS["picturebook"],
        today="2026-07-23",
        fetcher=fetch,
        session_factory=FakeSession,
        dedupe_rows=lambda _rows: [],
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]


def test_raw_network_is_opt_in_and_alias_target_does_not_fetch():
    rows, _parser, meta = gunpo.collect(gunpo.GUNPO_OWNERS["picturebook"])
    assert rows == []
    assert "raw requests disabled" in meta["configured_collection_error"]

    called = False

    def fetch(_session, _url, _timeout):
        nonlocal called
        called = True
        raise AssertionError

    rows, _parser, meta = gunpo.collect(
        {
            "provider": "MUNI_WWW_GUNPO_GO_KR_09F1E7BC",
            "url": "https://www.gunpo.go.kr/portal/index.do",
        },
        fetcher=fetch,
        session_factory=FakeSession,
    )
    assert rows == []
    assert not called
    assert "non-canonical" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_GUNPO_LIVE") != "1",
    reason="explicit Gunpo public-site integration audit only",
)
def test_live_all_nine_owners_are_stable_across_two_runs():
    snapshots = []
    for _run in range(2):
        run = {}
        for owner, target in gunpo.GUNPO_OWNERS.items():
            rows, _parser, meta = gunpo.collect(
                target,
                today="2026-07-23",
                allow_raw_requests_for_tests=True,
                max_workers=16,
            )
            assert meta["configured_collection_error"] == "", (owner, meta)
            assert meta["snapshot_complete"]
            assert meta["forbidden_endpoint_requests"] == 0
            run[owner] = (
                [row["provider_course_id"] for row in rows],
                meta["source_total_count"],
                meta["branch_counts"],
            )
        snapshots.append(run)
    assert snapshots[0] == snapshots[1]
