from __future__ import annotations

import hashlib
import os

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_hanam as hanam


TARGETS = (
    (hanam.HANAM_GSEEK_PROVIDER, hanam.HANAM_GSEEK_URL),
    (hanam.HANAM_RESIDENT_PROVIDER, hanam.HANAM_RESIDENT_URL),
    (hanam.HANAM_YOUTH_PROVIDER, hanam.HANAM_YOUTH_URL),
    (hanam.HANAM_HDREAM_PROVIDER, hanam.HANAM_HDREAM_URL),
    (hanam.HANAM_LIBRARY_PROVIDER, hanam.HANAM_LIBRARY_URL),
)


@pytest.mark.parametrize("provider,url", TARGETS)
def test_exact_canonical_owner_targets(provider: str, url: str) -> None:
    assert hanam.is_hanam_education_target({"provider": provider, "url": url})
    assert not hanam.is_hanam_education_target({"provider": provider, "url": url + "#fragment"})
    assert not hanam.is_hanam_education_target({"provider": provider, "url": url.replace("https://", "http://")})


def test_aliases_and_cross_owner_provider_pairs_are_rejected() -> None:
    assert not hanam.is_hanam_education_target(
        {"provider": hanam.HANAM_YOUTH_DEPRECATED_PROVIDER, "url": hanam.HANAM_YOUTH_URL}
    )
    assert not hanam.is_hanam_education_target(
        {"provider": hanam.HANAM_GSEEK_PROVIDER, "url": hanam.HANAM_GSEEK_PARENT_URL}
    )
    assert not hanam.is_hanam_education_target(
        {"provider": hanam.HANAM_HDREAM_PROVIDER, "url": hanam.HANAM_HDREAM_LEGACY_ALIAS}
    )
    assert not hanam.is_hanam_education_target(
        {"provider": hanam.HANAM_LIBRARY_PROVIDER, "url": hanam.HANAM_GSEEK_URL}
    )


@pytest.mark.parametrize("provider,url", TARGETS)
def test_production_collection_requires_managed_session(provider: str, url: str) -> None:
    rows, parser, meta = hanam.collect_hanam_education_courses(
        {"provider": provider, "url": url}, today="2026-07-23"
    )
    assert rows == []
    assert parser == hanam.HANAM_PARSER
    assert meta["snapshot_complete"] is False
    assert meta["application_endpoints_called"] == 0
    assert "session_factory" in meta["configured_collection_error"]


def test_candidate_ids_are_sha256_url_candidates() -> None:
    pairs = (
        (hanam.HANAM_GSEEK_CANDIDATE_ID, hanam.HANAM_GSEEK_URL),
        (hanam.HANAM_RESIDENT_CANDIDATE_ID, hanam.HANAM_RESIDENT_URL),
        (hanam.HANAM_HDREAM_CANDIDATE_ID, hanam.HANAM_HDREAM_URL),
        (hanam.HANAM_LIBRARY_CANDIDATE_ID, hanam.HANAM_LIBRARY_URL),
    )
    for candidate, url in pairs:
        assert candidate == "MUNI_IR_" + hashlib.sha256(url.encode()).hexdigest()[:12].upper()


def _gseek_item(*, sponsor: str = hanam.HANAM_GSEEK_CO_SPONSOR_ID) -> dict[str, object]:
    return {
        "d_sbjct_sn": "61907",
        "d_sbjct_cycl_sn": "1",
        "d_sbjct_nm": "시민 강좌",
        "d_edu_gvmnfc": "하남시평생학습관",
        "d_rgn": "신장1동",
        "d_co_sprvsn_id": sponsor,
        "d_recrut_stts_nm": "모집중",
        "d_edu_bgng_dt": "2026.08.01",
        "d_edu_end_dt": "2026.08.31",
        "d_edu_wday_cd_nm": "화",
        "d_edu_start_time": "10:00",
        "d_edu_end_time": "12:00",
        "d_sbjct_intrd_cn": "문의 031-790-0000 sample@example.org",
        "d_sbjct_amt": "0",
        "d_edu_nope": "20",
        "d_aply_cnt": "3",
    }


def test_gseek_row_owns_only_hanam_sponsor_and_redacts_pii() -> None:
    row = hanam._gseek_row(_gseek_item())
    assert row["provider_course_id"].endswith(":61907:1")
    assert row["description"] == "시민 강좌"
    assert row["collection_category"] == "공공예약"
    assert row["domain_category"] == "교육·강좌"
    assert row["source_group"] == "municipal_reservation"
    assert row["service_group"] == "공공강좌"
    assert row["service_group_policy"] == "locked"
    assert row["raw_fields"]["source_description_redacted"] is True
    assert "instructor" not in row and "phone" not in row
    with pytest.raises(hanam.HanamContractError, match="foreign"):
        hanam._gseek_row(_gseek_item(sponsor="G000999"))


def _resident_soup(title: str = "통기타 초급") -> BeautifulSoup:
    headers = ("동구분", "강좌명", "대상", "교육시간", "수강료", "강사명", "접수/정원", "문의전화", "접수방법", "신청하기")
    cells = ("천현동", title, "성인", "월 13:00~15:00", "없음", "홍길동", "6/10", "031-790-5916", "온라인(선착순)", "완료")
    markup = "<table><thead><tr>" + "".join(f"<th>{v}</th>" for v in headers) + "</tr></thead>"
    markup += "<tbody><tr>" + "".join(f"<td>{v}</td>" for v in cells) + "</tr></tbody></table>"
    return BeautifulSoup(markup, "lxml")


def test_resident_table_minimizes_instructor_and_contact() -> None:
    row = hanam._resident_rows(_resident_soup())[0]
    assert row["branch"] == "천현동"
    assert row["fee"] == "무료"
    assert "홍길동" not in str(row)
    assert "031-790-5916" not in str(row)
    assert row["raw_fields"]["term_snapshot_without_dates"] is True


@pytest.mark.parametrize("title", sorted(hanam.HANAM_RESIDENT_EXCLUDED_TITLES))
def test_resident_audited_test_titles_are_explicitly_marked(title: str) -> None:
    row = hanam._resident_rows(_resident_soup(title))[0]
    assert row["raw_fields"]["explicit_source_test_course"] is True


def test_youth_ajax_parser_accepts_leading_zero_identity_and_omits_contact() -> None:
    hidden = ("16", "14", "20260714", "20260724", "0900", "1800", "20260727", "50", "1")
    onclick = (
        "goLink('00500300092','%ED%8A%B9%EA%B0%95','005','999','002','02','1',"
        "'%ED%95%98%ED%95%98%EC%8A%A4%EC%BF%A8+1%EB%B0%98','01','01','15000','T','002')"
    )
    html = "".join(f'<input type="hidden" value="{value}">' for value in hidden)
    html += f"""<table><tr class="jone">
      <td>기타</td><td>특강</td><td><a onclick="{onclick}">강좌</a></td><td>초등</td>
      <td>월 09:30~11:30</td><td>01</td><td>15,000</td><td>14/16</td><td>접수불가</td><td>대기</td>
    </tr></table>"""
    row = hanam._youth_page(BeautifulSoup(html, "lxml"))[0]
    assert row["provider_course_id"].endswith(":00500300092")
    assert row["branch"] == "하남시청소년수련관"
    assert row["capacity_total"] == 16 and row["capacity_current"] == 14
    assert row["reservation_available"] is False


def test_hdream_row_uses_safe_public_fields_only() -> None:
    item = {
        "PROGRAM_ID": 75,
        "PROGRAM_TITLE": "진로설계 프로그램",
        "PROGRAM_APPLY_STATUS": "end",
        "APPLY_STATUS": "마감",
        "PRGROM_PERIOD": "2026.08.13 ~ 2026.08.13",
        "APPLY_PERIOD": "2026.07.13 00:00 ~ 2026.07.20 00:00",
        "SHOW_PLACE_NAME": "성균관대학교",
        "INTRODUCTION_MARKDOWN": "문의: 031-792-1661 담당자",
        "COST_YN": "N",
        "MAX_APPLY": 20,
        "APPLY_COUNT": 20,
    }
    row = hanam._hdream_row(item)
    assert row["description"] == "진로설계 프로그램"
    assert row["program_type"] == "진로체험"
    assert row["domain_category"] == "체험·견학"
    assert row["service_group"] == "체험"
    assert row["service_group_policy"] == "locked"
    assert row["raw_fields"]["source_description_redacted"] is True
    assert row["raw_url"].endswith("/75")
    assert "INTRODUCTION_MARKDOWN" not in str(row)


def _library_list_soup() -> BeautifulSoup:
    return BeautifulSoup("""
      <table><tbody><tr>
       <td>1</td><td>초등</td><td><a href="./selectWebEdcLctreView.do?key=72&amp;edcLctreNo=5016&amp;pageUnit=10&amp;pageIndex=1&amp;searchCnd=all">미디어 탐정단</a></td>
       <td>2026-08-14 ~ 2026-09-18</td><td>2026-07-21 10:00 ~ 2026-08-14 15:00</td>
       <td>4 / 12 / 0</td><td>접수중</td>
      </tr></tbody></table>
    """, "lxml")


def test_library_list_and_detail_only_discover_application_endpoint() -> None:
    row = hanam._library_page(_library_list_soup(), "나룰도서관", "nalib", "72")[0]
    detail = BeautifulSoup("""
      <table><tr><th>강좌명</th><td>미디어 탐정단</td></tr>
      <tr><th>일정</th><td>2026-08-14 ~ 2026-09-18</td></tr>
      <tr><th>강사명</th><td>홍길동</td></tr><tr><th>문의</th><td>031-000-0000</td></tr></table>
      <a href="./addEdcLctreReqstView.do?key=72&amp;edcLctreNo=5016">신청</a>
    """, "lxml")

    class SafeRunner:
        calls: list[str] = []

        def soup(self, method: str, url: str) -> BeautifulSoup:
            self.calls.append(url)
            return detail

    runner = SafeRunner()
    assert hanam._library_detail(runner, row) == 1
    assert runner.calls == [row["raw_url"]]
    assert "addEdcLctreReqstView.do" in row["application_url"]
    assert "홍길동" not in str(row) and "031-000-0000" not in str(row)


@pytest.mark.skipif(
    os.environ.get("RUN_HANAM_LIVE_AUDIT") != "1",
    reason="set RUN_HANAM_LIVE_AUDIT=1 for the bounded official-source census",
)
@pytest.mark.parametrize("owner,provider,url", (
    ("gseek", hanam.HANAM_GSEEK_PROVIDER, hanam.HANAM_GSEEK_URL),
    ("resident", hanam.HANAM_RESIDENT_PROVIDER, hanam.HANAM_RESIDENT_URL),
    ("youth", hanam.HANAM_YOUTH_PROVIDER, hanam.HANAM_YOUTH_URL),
    ("hdream", hanam.HANAM_HDREAM_PROVIDER, hanam.HANAM_HDREAM_URL),
    ("library", hanam.HANAM_LIBRARY_PROVIDER, hanam.HANAM_LIBRARY_URL),
))
def test_live_complete_hanam_owner_census(owner: str, provider: str, url: str) -> None:
    rows, _, meta = hanam.collect_hanam_education_courses(
        {"provider": provider, "url": url},
        today="2026-07-23",
        allow_raw_requests_for_tests=True,
        sleeper=lambda _: None,
    )
    baseline = hanam.HANAM_LIVE_AUDIT_BASELINE[owner]
    assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
    assert meta["source_total"] == baseline["source_total"]
    assert meta["current_count"] == baseline.get("current_count", baseline.get("returned_count"))
    assert len(rows) == meta["returned_count"]
    assert meta["pagination_complete"] and meta["details_complete"]
    assert meta["application_endpoints_called"] == 0
    assert all(row["municipality_code"] == "4145000000" for row in rows)
    assert all("instructor" not in row and "phone" not in row for row in rows)
