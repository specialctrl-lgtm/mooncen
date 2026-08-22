from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
import hashlib
import json
import os
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from Crawler import municipal_yangpyeong as yp
from utils.outbound_http import SafeSession


class Response:
    def __init__(self, url: str, payload, *, html: bool = False) -> None:
        self.url = url
        self.status_code = 200
        self.history = []
        self.headers = {
            "Content-Type": "text/html; charset=UTF-8" if html else "application/json"
        }
        if html:
            self.content = str(payload).encode("utf-8")
            self._payload = None
        else:
            self._payload = payload
            self.content = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


class Session:
    def close(self) -> None:
        return None


def target(owner: str) -> dict[str, str]:
    return dict(yp.YANGPYEONG_OWNERS[owner])


def gseek_item(
    subject: str,
    title: str,
    *,
    intro: str = "정상 강좌",
    branch: str = "매력캠퍼스(평생학습센터)",
    end: str = "2026.08.31",
) -> dict[str, object]:
    return {
        "d_total_cnt": "2",
        "d_sbjct_sn": subject,
        "d_sbjct_cycl_sn": "1",
        "d_sbjct_nm": title,
        "d_co_sprvsn_id": yp.YANGPYEONG_GSEEK_CO_SPONSOR_ID,
        "d_edu_gvmnfc": branch,
        "d_rgn": "양평읍",
        "d_edu_bgng_dt": "2026.08.01",
        "d_edu_end_dt": end,
        "d_edu_start_time": "10:00",
        "d_edu_end_time": "12:00",
        "d_edu_wday_cd_nm": "토",
        "d_sbjct_trgt_nm": "성인",
        "d_sbjct_amt": "0",
        "d_edu_nope": "10",
        "d_aply_cnt": "3",
        "d_stdnt_chice_mthd_cd_nm": "선착순",
        "d_clsf_depth1_nm": "문화예술",
        "d_clsf_depth2_nm": "생활문화",
        "d_recrut_stts_nm": "모집중",
        "d_sbjct_intrd_cn": intro,
    }


def gseek_detail(subject: str, title: str, *, apply: bool = False) -> str:
    control = '<a onclick="fnAply(); return false;">수강신청</a>' if apply else ""
    return f"""
      <form id="form1"><input name="s_sbjct_sn" value="{subject}">
      <input name="s_sbjct_cycl_sn" value="1"></form>
      <div id="div-offline-course-detail"><h2 class="course-title">{escape(title)}</h2>
      <p>담당자 010-1234-5678 private@example.com</p>{control}</div>
    """


@dataclass
class GseekFixture:
    bad_sentinel: bool = False
    bad_title: bool = False

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def fetch(self, _session, method, url, *, timeout, data=None):
        del timeout
        self.calls.append((method, url))
        parsed = urlparse(url)
        if parsed.path.endswith("/list/search"):
            assert method == "POST"
            start = int(data["s_row_start"])
            rows = [
                gseek_item("100", "정상 강좌"),
                gseek_item("101", "글쓰기", intro="테스트 강좌입니다!! 수강신청 NO !"),
            ] if start == 1 else []
            if self.bad_sentinel and start > 1:
                rows = [gseek_item("999", "sentinel drift")]
            return Response(url, rows)
        query = parse_qs(parsed.query)
        subject = query["s_sbjct_sn"][0]
        title = "changed" if self.bad_title and subject == "100" else (
            "정상 강좌" if subject == "100" else "글쓰기"
        )
        return Response(url, gseek_detail(subject, title, apply=subject == "101"), html=True)


def pool_row(class_cd: str, title: str, *, total: int, status: str = "R") -> dict[str, object]:
    return {
        "comcd": "YP21NET",
        "comnm": "양평군평생학습센터수영장",
        "class_cd": class_cd,
        "class_nm": title,
        "train_stime": "06:00",
        "train_etime": "06:50",
        "course_fee": "45,000",
        "status": status,
        "target_age_name": "성인, 청소년",
        "train_day_nm": "월수",
        "capa": "7",
        "reg_person": "1",
        "total_count": total,
        "category1": "수영",
        "category2": "새벽반",
        "teacher_name": "discard me",
    }


def pool_detail(title: str) -> str:
    return f"""
      <table class="fit"><tbody>
       <tr><th>강좌명</th><td>{escape(title)}</td></tr>
       <tr><th>운영센터</th><td>양평군평생학습센터수영장 /</td></tr>
       <tr><th>교육장소</th><td>수영장</td></tr>
       <tr><th>시간/요일</th><td>06:00 ~ 06:50 / 월수</td></tr>
       <tr><th>교육대상</th><td>성인, 청소년</td></tr>
       <tr><th>강사명</th><td>개인정보</td></tr>
       <tr><th>접수방식</th><td>선착접수</td></tr>
       <tr><th>신청인원/정원</th><td>1 / 7</td></tr>
      </tbody></table>
      <table id="fee_list"><thead><tr><th>선택</th><th>상품명</th><th>월 수강료</th><th>수강기간</th></tr></thead>
       <tbody><tr><td></td><td>성인강습</td><td>55,000원</td><td>1개월</td></tr></tbody></table>
      <table id="family_list"><thead><tr><th>생년월일</th></tr></thead></table>
    """


class PoolFixture:
    def __init__(self, *, empty_current: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.empty_current = empty_current

    def fetch(self, _session, method, url, *, timeout, data=None):
        del timeout
        self.calls.append((method, url))
        path = urlparse(url).path
        if path.endswith("/common/company"):
            return Response(url, [{"comcd": "YP21NET", "comnm": "양평군평생학습센터수영장"}])
        if path.endswith("/common/category"):
            return Response(url, [{"category_code": "1000000000", "category_name": "수영", "category_level": 1}])
        if path.endswith("/lecture/list"):
            page = int(data["page"])
            if page > 1:
                return Response(url, [])
            if data["search_type"] == "R":
                if self.empty_current:
                    return Response(url, [])
                return Response(url, [
                    pool_row("00100", "새벽 수영", total=2),
                    pool_row("00101", "마감된 현재반", total=2, status="E"),
                ])
            return Response(url, [pool_row("00001", "지난 수영", total=1, status="E")])
        title = "새벽 수영" if "classcd=00100" in url else "마감된 현재반"
        return Response(url, pool_detail(title), html=True)


def garden_row(no: str, title: str, start: str, end: str) -> str:
    return f"""<tr><td>{no}</td><td>{escape(title)}</td><td>1/10</td><td>쉬자파크</td>
      <td>{start}<br>{end}</td><td>2026-07-01 09:00<br>2026-07-31 18:00</td>
      <td>선착순</td><td>접수중</td><td><a href="./selectGardenEdcWebView.do?gardenNo={no}&key=3852">상세보기</a></td></tr>"""


def garden_list(page: int, *, bad_sentinel: bool = False) -> str:
    rows = (
        garden_row("10", "정상 정원 강좌", "2026-08-01", "2026-08-31")
        + garden_row("11", "식물 전시회", "2026-08-02", "2026-08-02")
        if page == 1
        else garden_row("99", "sentinel drift", "2026-08-01", "2026-08-31") if bad_sentinel else ""
    )
    return f"""<div class="post-all">총게시물 : <em>2</em>건</div>
      <div class="post-page">페이지 : <em>{page}</em>/1</div>
      <table><tbody class="text_center">{rows}</tbody></table>"""


def garden_detail(title: str) -> str:
    return f"""<table><tbody>
      <tr><th>프로그램명</th><td>{escape(title)}</td></tr><tr><th>장소</th><td>쉬자파크</td></tr>
      <tr><th>참가비</th><td>무료</td></tr><tr><th>수업일</th><td>2026-08-01 ~ 2026-08-31</td></tr>
      <tr><th>수업시간</th><td>10:00 ~ 12:00</td></tr><tr><th>신청기간</th><td>2026-07-01 ~ 2026-07-31</td></tr>
      <tr><th>모집방법</th><td>선착순</td></tr><tr><th>모집인원</th><td>10 명</td></tr>
      <tr><th>모집내용</th><td>010-1234-5678</td></tr><tr><th>교육내용</th><td>private@example.com</td></tr>
      </tbody></table><a href="./addGardenEdcReqstWebView.do?gardenNo=10">신청</a>"""


@dataclass
class GardenFixture:
    bad_sentinel: bool = False

    def fetch(self, _session, method, url, *, timeout, data=None):
        del timeout, data
        assert method == "GET"
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("selectGardenEdcWebList.do"):
            return Response(url, garden_list(int(query["pageIndex"][0]), bad_sentinel=self.bad_sentinel), html=True)
        no = query["gardenNo"][0]
        title = "정상 정원 강좌" if no == "10" else "식물 전시회"
        html = garden_detail(title)
        if no == "11":
            html = html.replace(
                "2026-08-01 ~ 2026-08-31",
                "2026-08-02 ~ 2026-08-02",
                1,
            )
        return Response(url, html, html=True)


def library_item(
    rec_key: str,
    title: str,
    *,
    end: str = "2026-08-31 00:00:00",
    start: str = "2026-08-01 00:00:00",
    code: str = "ALL",
) -> dict[str, object]:
    return {
        "recKey": rec_key,
        "manageCode": code,
        "eventName": title,
        "eventTarget": "성인",
        "eventTeacher": "discard",
        "applicationCnt": "10",
        "waitCnt": "2",
        "eventContent": "담당 010-1234-5678 private@example.com",
        "eventStartDate": start,
        "eventEndDate": end,
        "eventTime": "10:00~12:00",
        "takeStartDate": "2026-07-01 09:00:00",
        "takeEndDate": "2026-07-31 18:00:00",
        "eventStateDesc": "진행",
        "applyEnableStatusDesc": "신청하기",
        "inputWorker": "담당자",
        "userApplicationCnt": "3",
        "userList": [{"phone": "010-0000-0000"}],
        "fileList": [{"path": "/private"}],
    }


class LibraryFixture:
    def __init__(self, *, reversed_confirmation: bool = False) -> None:
        self.items = [
            library_item("1", "양평도서관 여름 특강 수강생 모집"),
            library_item(
                "2",
                "양평도서관 여름 특강 수강 확정자 명단 발표",
                start="2026-09-01 00:00:00" if reversed_confirmation else "2026-08-01 00:00:00",
                end="2026-08-01 00:00:00" if reversed_confirmation else "2026-08-31 00:00:00",
            ),
            library_item("3", "구독형 독서콘텐츠 이용자 모집"),
            library_item("4", "지난 문화 프로그램", start="2025-01-01 00:00:00", end="2025-01-31 00:00:00"),
        ]

    def envelope(self, page: int):
        return {
            "status": "OK",
            "message": "",
            "data": {
                "totalCount": 4,
                "totalPage": 1,
                "pageIndex": page,
                "pageSize": 10,
                "data": self.items if page == 1 else [],
            },
        }

    def fetch(self, _session, method, url, *, timeout, data=None):
        del timeout, data
        assert method == "GET"
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path.endswith("/event/list"):
            return Response(url, self.envelope(int(query["pageIndex"][0])))
        rec_key = query["recKey"][0]
        return Response(url, {"status": "OK", "message": "", "data": next(item for item in self.items if item["recKey"] == rec_key)})


def collect_fixture(owner: str, fixture, **kwargs):
    return yp.collect(
        target(owner),
        today="2026-07-23",
        max_workers=1,
        session_factory=Session,
        fetcher=fixture.fetch,
        **kwargs,
    )


def test_exact_targets_candidate_ids_and_provider_hashes():
    assert set(yp.YANGPYEONG_OWNERS) == {"gseek", "pool", "garden", "library"}
    for owner, config in yp.YANGPYEONG_OWNERS.items():
        assert yp.owner_for_target(config) == owner
        assert yp.is_target(config)
        candidate = "MUNI_IR_" + hashlib.sha256(config["url"].encode()).hexdigest()[:12].upper()
        assert config["candidate_id"] == candidate
    for owner in ("pool", "garden", "library"):
        config = yp.YANGPYEONG_OWNERS[owner]
        provider_hash = hashlib.sha1(config["url"].encode()).hexdigest()[:8].upper()
        assert config["provider"].endswith("_" + provider_hash)
    assert not yp.is_target(
        {"provider": yp.YANGPYEONG_GSEEK_PROVIDER, "url": yp.YANGPYEONG_LEGACY_GSEEK_URL}
    )


def test_official_resident_and_library_branch_registries():
    assert len(yp.YANGPYEONG_RESIDENT_CENTRES) == 12
    assert len(set(yp.YANGPYEONG_RESIDENT_CENTRES)) == 12
    assert set(yp.YANGPYEONG_LIBRARY_BRANCHES) == {
        "MA", "MD", "ME", "MC", "MH", "MI", "MF", "MB", "MG", "MM", "MJ", "ML", "ZA"
    }
    assert yp.YANGPYEONG_LIBRARY_BRANCHES["MA"] == "양평도서관"
    assert yp.YANGPYEONG_LIBRARY_BRANCHES["ML"] == "개군작은도서관"


def test_only_public_registry_list_and_detail_routes_are_allowed():
    safe = [
        ("gseek", "POST", "https://ypedu.gseek.kr/user/course/offline/list/search", yp.gseek_list_data(1), "list"),
        ("gseek", "GET", yp.gseek_detail_url("1", "1"), None, "detail"),
        ("pool", "POST", "https://www.yp21.go.kr/pool/rest/common/company", yp.pool_company_data(), "registry"),
        ("pool", "POST", "https://www.yp21.go.kr/pool/rest/common/category", yp.pool_category_data(), "registry"),
        ("pool", "POST", "https://www.yp21.go.kr/pool/rest/lecture/list", yp.pool_list_data("R", 1), "list"),
        ("pool", "GET", yp.pool_detail_url("00100"), None, "detail"),
        ("garden", "GET", yp.garden_list_url(1), None, "list"),
        ("garden", "GET", yp.garden_detail_url("10"), None, "detail"),
        ("library", "GET", yp.library_list_url(1), None, "list"),
        ("library", "GET", yp.library_detail_url("1"), None, "detail"),
    ]
    for owner, method, url, data, kind in safe:
        assert yp._classify_url(owner, method, url, data) == kind

    forbidden = [
        ("gseek", "POST", "https://ypedu.gseek.kr/user/course/offline/aply", {"name": "x"}),
        ("gseek", "GET", "https://ypedu.gseek.kr/user/userLogin", None),
        ("pool", "POST", "https://www.yp21.go.kr/pool/rest/lecture/family", {"mem_no": "1"}),
        ("pool", "GET", "https://www.yp21.go.kr/pool/fmcs/16", None),
        ("garden", "GET", "https://www.yp21.go.kr/ypjeongwon/addGardenEdcReqstWebView.do?key=3852&gardenNo=10", None),
        ("library", "GET", "https://www.yplib.go.kr/user/service/culture/event/apply/detail?recKey=1", None),
        ("library", "POST", "https://www.yplib.go.kr/user/service/culture/event/insert", {"userTel": "x"}),
    ]
    for owner, method, url, data in forbidden:
        with pytest.raises(yp.YangpyeongContractError):
            yp._classify_url(owner, method, url, data)


def test_gseek_complete_snapshot_excludes_test_but_never_requests_application():
    fixture = GseekFixture()
    rows, parser, meta = collect_fixture("gseek", fixture)
    assert parser == yp.YANGPYEONG_PARSER
    assert [row["title"] for row in rows] == ["정상 강좌"]
    assert meta["source_total"] == meta["current_source_count"] == meta["detail_verified"] == 2
    assert meta["excluded_counts"] == {"test_record": 1}
    assert meta["empty_sentinel_page"] == 2 and all(meta["boundary_rechecks"].values())
    assert meta["snapshot_complete"] and meta["application_endpoint_requests"] == 0
    assert meta["parent_aggregate_exclusion_required"] is True
    assert meta["parent_aggregate_exclusion_value"] == "G000012"
    assert all(row["application_url"] == "" for row in rows)
    assert not any("aply" in url.lower() for _method, url in fixture.calls)


def test_transient_transport_errors_refresh_session_and_retry(monkeypatch):
    fixture = GseekFixture()
    original_fetch = fixture.fetch
    failures = 2

    def flaky_fetch(*args, **kwargs):
        nonlocal failures
        if failures:
            failures -= 1
            raise requests.ConnectionError("transient reset")
        return original_fetch(*args, **kwargs)

    fixture.fetch = flaky_fetch
    monkeypatch.setattr(yp.time, "sleep", lambda _seconds: None)
    rows, _parser, meta = collect_fixture("gseek", fixture)
    assert len(rows) == 1 and meta["snapshot_complete"]
    assert meta["request_retry_count"] == 2
    assert meta["physical_requests"] == meta["logical_requests"] + 2


def test_pool_complete_current_and_ended_partitions_are_disjoint():
    rows, _parser, meta = collect_fixture("pool", PoolFixture())
    assert len(rows) == 2
    assert {row["source_course_id"] for row in rows} == {"YP21NET:00100", "YP21NET:00101"}
    assert meta["partition_totals"] == {"R": 2, "E": 1}
    assert meta["source_total"] == 3 and meta["current_source_count"] == 2
    assert meta["partition_identity_disjoint"] and meta["registry_requests"] == 2
    assert meta["branch_counts"] == {"양평군평생학습센터수영장": 2}
    assert meta["application_endpoint_requests"] == meta["login_endpoint_requests"] == 0


def test_pool_stable_empty_current_partition_is_complete_no_current_data():
    rows, _parser, meta = collect_fixture(
        "pool",
        PoolFixture(empty_current=True),
    )

    assert rows == []
    assert meta["partition_totals"] == {"R": 0, "E": 1}
    assert meta["empty_sentinel_pages"] == {"R": 2, "E": 2}
    assert meta["current_source_count"] == 0
    assert meta["detail_verified"] == 0
    assert meta["required_list_requests"] == 8
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"]


def test_garden_complete_snapshot_excludes_non_course_exhibition():
    rows, _parser, meta = collect_fixture("garden", GardenFixture())
    assert [row["source_course_id"] for row in rows] == ["10"]
    assert meta["source_total"] == meta["current_source_count"] == meta["detail_verified"] == 2
    assert meta["excluded_counts"] == {"cancelled_or_exhibition_not_course": 1}
    assert meta["branch_counts"] == {"양평정원": 1}
    assert meta["application_endpoint_requests"] == 0


def test_library_full_ledger_excludes_results_and_services_and_discards_pii():
    rows, _parser, meta = collect_fixture("library", LibraryFixture())
    assert [row["source_course_id"] for row in rows] == ["1"]
    assert rows[0]["branch"] == "양평도서관"
    assert meta["source_total"] == 4 and meta["current_source_count"] == 3
    assert meta["detail_verified"] == 3 and meta["returned_count"] == 1
    assert meta["excluded_counts"] == {
        "confirmation_or_result_duplicate": 1,
        "subscription_or_lending_service": 1,
    }
    assert meta["pii_values_persisted"] == 0
    assert "010-1234-5678" not in repr(rows) and "private@example.com" not in repr(rows)


def test_historical_reversed_confirmation_is_normalized_and_counted():
    rows, _parser, meta = collect_fixture(
        "library", LibraryFixture(reversed_confirmation=True)
    )
    assert len(rows) == 1
    assert meta["historical_confirmation_date_reversed_count"] == 1


@pytest.mark.parametrize(
    ("owner", "fixture", "fragment"),
    [
        ("gseek", GseekFixture(bad_sentinel=True), "sentinel"),
        ("gseek", GseekFixture(bad_title=True), "detail title drift"),
        ("garden", GardenFixture(bad_sentinel=True), "sentinel"),
    ],
)
def test_contract_drift_fails_closed(owner, fixture, fragment):
    rows, _parser, meta = collect_fixture(owner, fixture)
    assert rows == []
    assert fragment in meta["configured_collection_error"]
    assert not meta["snapshot_complete"]


def test_caps_and_raw_network_default_fail_closed_before_details():
    rows, _parser, meta = collect_fixture("gseek", GseekFixture(), max_pages=3)
    assert rows == [] and meta["source_cap_reached"] and meta["detail_requests"] == 0
    rows, _parser, meta = yp.collect(target("garden"), today="2026-07-23")
    assert rows == [] and "raw requests disabled" in meta["configured_collection_error"]


def test_library_managed_adapter_preserves_safe_session():
    session = yp._library_managed_session(SafeSession)
    try:
        assert isinstance(session, SafeSession)
        assert isinstance(
            session.get_adapter("https://www.yplib.go.kr/"),
            yp._YangpyeongLibraryPinnedAdapter,
        )
        context = yp._library_certificate_context()
        assert context.verify_mode != 0 and context.check_hostname
    finally:
        session.close()


def test_output_allowlist_has_no_pii_or_free_text_fields():
    snapshots = [
        collect_fixture("gseek", GseekFixture())[0],
        collect_fixture("pool", PoolFixture())[0],
        collect_fixture("garden", GardenFixture())[0],
        collect_fixture("library", LibraryFixture())[0],
    ]
    forbidden = {
        "phone", "email", "contact", "manager", "instructor", "teacher",
        "description", "content", "attachments", "attachment_url", "image_url",
        "request_form", "applicant", "user_list",
    }
    for rows in snapshots:
        for row in rows:
            assert not (set(row) & forbidden)
            assert row["application_url"] == ""
            assert not yp._privacy_errors(row)


@pytest.mark.skipif(
    os.environ.get("RUN_YANGPYEONG_LIVE") != "1", reason="opt-in two-pass live audit"
)
def test_two_pass_live_stability():
    summaries = []
    for _pass in range(2):
        current = {}
        for owner, config in yp.YANGPYEONG_OWNERS.items():
            rows, _parser, meta = yp.collect(
                config,
                today=date(2026, 7, 23),
                max_workers=4,
                allow_raw_requests_for_tests=True,
            )
            assert meta["snapshot_complete"], meta.get("configured_collection_error")
            current[owner] = (
                meta["source_total"],
                len(rows),
                meta["source_identity_sha256"],
                meta["output_identity_sha256"],
            )
        summaries.append(current)
    assert summaries[0] == summaries[1]
