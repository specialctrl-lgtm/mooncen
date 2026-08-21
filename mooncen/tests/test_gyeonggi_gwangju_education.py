from __future__ import annotations

import hashlib
import os
from typing import Any

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_gyeonggi_gwangju as gj
from tools.promote_municipal_integrated_reservation_targets import normalized_duplicate_url


TARGETS = (
    (gj.GYEONGGI_GWANGJU_GSEEK_PROVIDER, gj.GYEONGGI_GWANGJU_GSEEK_URL),
    (gj.GYEONGGI_GWANGJU_RESIDENT_PROVIDER, gj.GYEONGGI_GWANGJU_RESIDENT_URL),
    (gj.GYEONGGI_GWANGJU_LIBRARY_PROVIDER, gj.GYEONGGI_GWANGJU_LIBRARY_URL),
    (gj.GYEONGGI_GWANGJU_IT_PROVIDER, gj.GYEONGGI_GWANGJU_IT_URL),
    (gj.GYEONGGI_GWANGJU_AGRI_PROVIDER, gj.GYEONGGI_GWANGJU_AGRI_URL),
    (gj.GYEONGGI_GWANGJU_YOUTH_PROVIDER, gj.GYEONGGI_GWANGJU_YOUTH_URL),
)


@pytest.mark.parametrize("provider,url", TARGETS)
def test_exact_canonical_owner_targets(provider: str, url: str) -> None:
    assert gj.is_gyeonggi_gwangju_education_target({"provider": provider, "url": url})
    assert not gj.is_gyeonggi_gwangju_education_target(
        {"provider": provider, "url": url + "#fragment"}
    )
    assert not gj.is_gyeonggi_gwangju_education_target(
        {"provider": provider, "url": url.replace("https://", "http://")}
    )
    assert not gj.is_gyeonggi_gwangju_education_target(
        {"provider": provider, "url": url.replace("https://", "https://user:pw@")}
    )


def test_cross_owner_aliases_and_gwangju_metropolitan_city_are_rejected() -> None:
    assert not gj.is_gyeonggi_gwangju_education_target({
        "provider": gj.GYEONGGI_GWANGJU_GSEEK_PROVIDER,
        "url": gj.GYEONGGI_GWANGJU_GSEEK_PARENT_URL,
    })
    assert not gj.is_gyeonggi_gwangju_education_target({
        "provider": gj.GYEONGGI_GWANGJU_IT_PROVIDER,
        "url": gj.GYEONGGI_GWANGJU_IT_ALIAS_URL,
    })
    assert not gj.is_gyeonggi_gwangju_education_target({
        "provider": gj.GYEONGGI_GWANGJU_LIBRARY_PROVIDER,
        "url": gj.GYEONGGI_GWANGJU_METROPOLITAN_URL,
    })
    assert not gj.is_gyeonggi_gwangju_education_target({
        "provider": gj.GYEONGGI_GWANGJU_YOUTH_PROVIDER,
        "url": gj.GYEONGGI_GWANGJU_YOUTH_SPORTS_URLS[0],
    })


@pytest.mark.parametrize("provider,url", TARGETS)
def test_production_collection_requires_managed_session(provider: str, url: str) -> None:
    rows, parser, meta = gj.collect_gyeonggi_gwangju_education_courses(
        {"provider": provider, "url": url}, today="2026-07-23"
    )
    assert rows == []
    assert parser == gj.GYEONGGI_GWANGJU_PARSER
    assert meta["snapshot_complete"] is False
    assert meta["application_endpoints_called"] == 0
    assert "session_factory" in meta["configured_collection_error"]


def test_provider_and_candidate_ids_are_url_hashes() -> None:
    pairs = (
        (gj.GYEONGGI_GWANGJU_GSEEK_PROVIDER, gj.GYEONGGI_GWANGJU_GSEEK_CANDIDATE_ID, gj.GYEONGGI_GWANGJU_GSEEK_URL),
        (gj.GYEONGGI_GWANGJU_RESIDENT_PROVIDER, gj.GYEONGGI_GWANGJU_RESIDENT_CANDIDATE_ID, gj.GYEONGGI_GWANGJU_RESIDENT_URL),
        (gj.GYEONGGI_GWANGJU_LIBRARY_PROVIDER, gj.GYEONGGI_GWANGJU_LIBRARY_CANDIDATE_ID, gj.GYEONGGI_GWANGJU_LIBRARY_URL),
        (gj.GYEONGGI_GWANGJU_IT_PROVIDER, gj.GYEONGGI_GWANGJU_IT_CANDIDATE_ID, gj.GYEONGGI_GWANGJU_IT_URL),
        (gj.GYEONGGI_GWANGJU_AGRI_PROVIDER, gj.GYEONGGI_GWANGJU_AGRI_CANDIDATE_ID, gj.GYEONGGI_GWANGJU_AGRI_URL),
        (gj.GYEONGGI_GWANGJU_YOUTH_PROVIDER, gj.GYEONGGI_GWANGJU_YOUTH_CANDIDATE_ID, gj.GYEONGGI_GWANGJU_YOUTH_URL),
    )
    for provider, candidate, url in pairs:
        host = url.split("//", 1)[1].split("/", 1)[0].replace(".", "_").upper()
        assert provider == f"MUNI_{host}_{hashlib.sha1(url.encode()).hexdigest()[:8].upper()}"
        normalized_url = normalized_duplicate_url(url)
        assert candidate == "MUNI_IR_" + hashlib.sha256(normalized_url.encode()).hexdigest()[:12].upper()


def test_exact_official_branch_registries_and_owner_boundaries() -> None:
    assert len(gj.GYEONGGI_GWANGJU_RESIDENT_BRANCHES) == 15
    assert dict(gj.GYEONGGI_GWANGJU_RESIDENT_BRANCHES) == {
        "2": "초월읍", "3": "곤지암읍", "4": "도척면", "5": "퇴촌남종면",
        "6": "남한산성면", "1": "오포1동", "13": "오포2동", "14": "신현동",
        "15": "능평동", "7": "경안동", "11": "쌍령동", "16": "송정동",
        "8": "탄벌동", "12": "광남1동", "9": "광남2동",
    }
    assert [branch for branch, _ in gj.GYEONGGI_GWANGJU_LIBRARY_BRANCHES] == [
        "중앙도서관", "오포도서관", "초월도서관", "곤지암도서관", "능평도서관",
        "양벌도서관", "광남도서관", "퇴촌도서관", "만선도서관", "신현도서관",
        "작은도서관",
    ]
    assert gj.GYEONGGI_GWANGJU_GSEEK_BRANCHES == (
        "광주시 평생학습관",
        "여성비전센터",
        "검천평생학습센터",
        "송정 청소년 문화의 집",
        "신현 청소년 문화의 집",
        "광주시 읍면동 평생학습센터",
        "광주시 장애인평생학습센터",
    )
    assert gj.GYEONGGI_GWANGJU_OWNER_BOUNDARY_AUDIT["gwangju_metropolitan_city"]["decision"].startswith("hard_exclude")
    assert "separate_sports" in gj.GYEONGGI_GWANGJU_OWNER_BOUNDARY_AUDIT["municipal_sports"]["decision"]


class _NeverSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url: str, **_: Any) -> None:
        self.calls.append(url)
        raise AssertionError("network must not be reached")


@pytest.mark.parametrize("url", (
    gj.GYEONGGI_GWANGJU_RESIDENT_APPLICATION_ENDPOINT,
    gj.GYEONGGI_GWANGJU_IT_APPLICATION_ENDPOINT,
    gj.GYEONGGI_GWANGJU_AGRI_APPLICATION_ENDPOINT,
))
def test_runner_refuses_application_endpoints_before_network(url: str) -> None:
    session = _NeverSession()
    runner = gj._Runner(lambda: session, 10, 10, lambda _: None)
    with pytest.raises(gj.GyeonggiGwangjuContractError, match="application endpoint"):
        runner.request("get", url)
    assert session.calls == []


def _gseek_item(*, sponsor: str = gj.GYEONGGI_GWANGJU_GSEEK_CO_SPONSOR_ID) -> dict[str, Any]:
    return {
        "d_sbjct_sn": "80234",
        "d_sbjct_cycl_sn": "1",
        "d_sbjct_nm": "시민 공개 강좌",
        "d_edu_gvmnfc": "광주시 평생학습관",
        "d_rgn": "송정동",
        "d_co_sprvsn_id": sponsor,
        "d_recrut_stts_nm": "추가접수",
        "d_edu_bgng_dt": "2026.08.01",
        "d_edu_end_dt": "2026.08.31",
        "d_edu_wday_cd_nm": "화",
        "d_edu_start_time": "10:00",
        "d_edu_end_time": "12:00",
        "d_sbjct_intrd_cn": "문의 031-760-0000 privacy@example.org",
        "d_sbjct_amt": "0",
        "d_edu_nope": "20",
        "d_aply_cnt": "3",
    }


def test_gseek_partition_is_exact_and_pii_description_is_not_persisted() -> None:
    row = gj._gseek_row(_gseek_item())
    assert row["provider_course_id"].endswith(":80234:1")
    assert row["branch"] == "광주시 평생학습관"
    assert row["preserve_branch"] is True
    assert row["venue_name"] == "광주시 평생학습관"
    assert row["raw_fields"]["co_sponsor_id"] == "G000007"
    assert "031-760-0000" not in str(row)
    assert "privacy@example.org" not in str(row)
    assert "instructor" not in row and "phone" not in row
    with pytest.raises(gj.GyeonggiGwangjuContractError, match="foreign"):
        gj._gseek_row(_gseek_item(sponsor="G000001"))

    women = _gseek_item()
    women["d_edu_gvmnfc"] = "여성비전센터"
    women_row = gj._gseek_row(women)
    assert women_row["branch"] == "여성비전센터"
    assert women_row["venue_name"] == "여성비전센터"


def _resident_soup(title: str = "탁구(야간)") -> BeautifulSoup:
    return BeautifulSoup(f"""
      <table><thead>
       <tr><th>번호</th><th>읍면동</th><th>프로그램명</th><th>접수기간</th>
       <th>교육기간</th><th>접수자/정원</th><th>접수방법</th><th>접수상태</th></tr>
       <tr><th>교육시간 및 요일</th><th>수강료</th></tr>
      </thead><tbody>
       <tr><td>1</td><td>[쌍령동] 2026년 3기</td>
       <td><button data-button="view" data-idx="8307">{title} 상세보기</button></td>
       <td>2026-06-23 ~ 2026-09-25</td><td>2026-07-02 18:30 ~ 2026-09-24 20:00</td>
       <td>16명 / 16명</td><td>온라인,방문</td><td>접수중</td></tr>
       <tr><td>18:30 ~ 20:00 (화,목)</td><td>75,000원</td></tr>
      </tbody></table>
    """, "lxml")


def test_resident_row_uses_exact_branch_and_marks_source_test() -> None:
    row = gj._resident_page(_resident_soup(), "11", "쌍령동")[0]
    assert row["provider_course_id"].endswith(":8307")
    assert row["branch"] == "쌍령동"
    assert row["capacity_total"] == 16 and row["capacity_current"] == 16
    assert row["raw_fields"]["explicit_source_test_course"] is False
    test_row = gj._resident_page(_resident_soup("test"), "11", "쌍령동")[0]
    assert test_row["raw_fields"]["explicit_source_test_course"] is True


def test_resident_detail_discovers_but_does_not_call_application_endpoint() -> None:
    row = gj._resident_page(_resident_soup(), "11", "쌍령동")[0]
    detail = BeautifulSoup("""
      <table><tr><th>강좌명</th><td>탁구(야간)</td></tr>
      <tr><th>접수기간</th><td>2026-06-23 09:00 ~ 2026-09-25 23:30</td>
          <th>교육장소</th><td>쌍령핑퐁클럽</td></tr>
      <tr><th>교육기간</th><td>2026-07-02 ~ 2026-09-24 화,목</td>
          <th>대상</th><td>광주시민</td></tr>
      <tr><th>문의전화</th><td>031-762-1907</td><th>강사명</th><td>홍길동</td></tr></table>
      <button data-button="write" data-program-idx="8307">신청</button>
    """, "lxml")

    class SafeRunner:
        calls: list[str] = []

        def soup(self, method: str, url: str, *, parameterized: bool = False) -> BeautifulSoup:
            self.calls.append(url)
            return detail

    runner = SafeRunner()
    assert gj._resident_detail(runner, row) == 1
    assert runner.calls == [row["raw_url"]]
    assert row["application_url"].startswith(gj.GYEONGGI_GWANGJU_RESIDENT_APPLICATION_ENDPOINT)
    assert "홍길동" not in str(row) and "031-762-1907" not in str(row)


def _library_soup() -> BeautifulSoup:
    return BeautifulSoup("""
      <div class="program-list"><dl>
       <dt><p><span class="ja">중앙</span></p>
       <a href="cultureWrt_wrt.do?fn_seq=74381&amp;rows=10&amp;cpage=1">
       독서동아리 신규 회원 모집 &lt;우리가 만드는 북모임&gt;</a></dt>
       <dd>강좌기간 : 2026-08-13 ~ 2026-12-31</dd>
       <dd>강좌시간 : 목 19:00 ~ 21:00</dd><dd>강좌대상 : 성인</dd>
       <dd>접수기간 : 2026-07-20 ~ 2026-08-12</dd>
       <dd class="btn_res">접수중</dd>
      </dl></div>
      <a href="?rows=10&amp;cpage=120">120</a>
    """, "lxml")


def test_library_card_parser_uses_official_branch_and_fn_sequence() -> None:
    rows = gj._library_page(
        _library_soup(), "중앙도서관", gj.GYEONGGI_GWANGJU_LIBRARY_URL
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["provider_course_id"].endswith(":74381")
    assert row["branch"] == "중앙도서관"
    assert row["end_date"] == "2026-12-31"
    assert row["status"] == "OPEN"
    assert gj._advertised_last_page(_library_soup()) == 120


def test_library_rate_limit_page_is_not_misread_as_empty_catalogue() -> None:
    class Response:
        status_code = 200
        history: list[Any] = []
        url = gj.GYEONGGI_GWANGJU_LIBRARY_URL
        content = "비정상적으로 빠른 요청이 감지되어 이용이 제한되었습니다.".encode("cp949")

    with pytest.raises(gj.GyeonggiGwangjuContractError, match="rate-limit"):
        gj._validate_response(Response(), gj.GYEONGGI_GWANGJU_LIBRARY_URL)


def _it_soup() -> BeautifulSoup:
    return BeautifulSoup("""
      <table><thead><tr><th>년/월</th><th>접수 구분</th><th>교육 유형</th><th>강좌명</th>
       <th>신청기간</th><th>교육기간</th><th>접수자/정원 (예비자/정원)</th><th>상태</th>
      </tr></thead><tbody><tr><td>2026/08</td><td>초급</td><td>집합</td>
       <td><a data-button="view" data-idx="834">한글(초급)</a></td>
       <td>2026-07-20 09:00 ~ 2026-07-29 18:00</td>
       <td>2026-08-03 ~ 2026-08-31 월,수 (11:40 ~ 13:10)</td>
       <td>16/30 (0/3)</td><td>접수중</td></tr></tbody></table>
    """, "lxml")


def test_citizen_it_row_is_current_education_with_stable_identity() -> None:
    row = gj._it_page(_it_soup())[0]
    assert row["provider_course_id"].endswith(":834")
    assert row["branch"] == "광주시 시민정보화교육장"
    assert row["capacity_total"] == 30 and row["capacity_current"] == 16
    assert row["reservation_available"] is True


def _agri_soup() -> BeautifulSoup:
    return BeautifulSoup("""
      <table><thead><tr><th>번호</th><th>교육명</th><th>신청기간</th><th>교육기간</th>
       <th>접수자/정원 (예비자/정원)</th><th>상태</th><th>신청방식</th></tr></thead>
       <tbody><tr><td>254</td><td><a data-button="view" data-idx="725">
       광주시 자연채 푸드팜 센터 요리강좌 수강생 모집(주중반)</a></td>
       <td>2026-06-25 10:00 ~ 2026-07-13 18:00</td>
       <td>2026-07-14 ~ 2026-09-01 화 (10:00 ~ 13:00)</td>
       <td>16/16 (12/16)</td><td>교육중</td><td><a data-button="write" data-idx="725"
       data-tel="031-760-2873" data-org="광주시농업기술센터 농업정책과">온라인접수</a></td>
       </tr></tbody></table>
    """, "lxml")


def test_agricultural_row_does_not_persist_contact_attributes() -> None:
    row = gj._agri_page(_agri_soup())[0]
    assert row["provider_course_id"].endswith(":725")
    assert row["branch"] == "광주시농업기술센터"
    assert row["end_date"] == "2026-09-01"
    assert "031-760-2873" not in str(row)
    assert row["raw_fields"]["detail_description_omitted_for_pii"] is True


def _youth_soup() -> BeautifulSoup:
    return BeautifulSoup("""
      <div class="listArea02"><ul><li><p>
       <a href="JavaScript:view_d('2422','1','life_y_v');"><img title="과학교실 A"></a></p>
       <dl><dt><a href="JavaScript:view_d('2422','1','life_y_v');">과학교실 A</a></dt>
       <dd>• 강 사 명 : 김재욱</dd><dd>• 교육대상 : 초1~초2</dd><dd>• 요 일 : 토</dd>
       <dd>• 교육시간 : 13:30 ~ 14:50</dd><dd>• 수강정원 : 12</dd>
       <dd>• 수 강 료 : 30,000원</dd><dd>• 현 재 : 강좌 진행중[미달:1명]</dd></dl>
      </li></ul></div><div class="paging"><strong>1</strong>
      <a onclick="javascript:page_l('7','life_y')">7</a></div>
    """, "lxml")


def test_youth_lifelong_parser_omits_instructor_and_keeps_sports_out() -> None:
    row = gj._youth_page(_youth_soup(), "평생교육(청소년)", "life_y.asp", "life_y_v.asp")[0]
    assert row["provider_course_id"].endswith(":2422")
    assert row["branch"] == "광주시청소년수련관"
    assert row["category"] == "평생교육(청소년)"
    assert row["reservation_available"] is True
    assert "김재욱" not in str(row)
    assert row["raw_fields"]["term_snapshot_without_dates"] is True
    assert gj._advertised_last_page(_youth_soup()) == 7


@pytest.mark.skipif(
    os.environ.get("RUN_GYEONGGI_GWANGJU_LIVE_AUDIT") != "1",
    reason="set RUN_GYEONGGI_GWANGJU_LIVE_AUDIT=1 for two bounded official-source censuses",
)
@pytest.mark.parametrize("owner,provider,url", (
    ("gseek", gj.GYEONGGI_GWANGJU_GSEEK_PROVIDER, gj.GYEONGGI_GWANGJU_GSEEK_URL),
    ("resident", gj.GYEONGGI_GWANGJU_RESIDENT_PROVIDER, gj.GYEONGGI_GWANGJU_RESIDENT_URL),
    ("library", gj.GYEONGGI_GWANGJU_LIBRARY_PROVIDER, gj.GYEONGGI_GWANGJU_LIBRARY_URL),
    ("citizen_it", gj.GYEONGGI_GWANGJU_IT_PROVIDER, gj.GYEONGGI_GWANGJU_IT_URL),
    ("agriculture", gj.GYEONGGI_GWANGJU_AGRI_PROVIDER, gj.GYEONGGI_GWANGJU_AGRI_URL),
    ("youth", gj.GYEONGGI_GWANGJU_YOUTH_PROVIDER, gj.GYEONGGI_GWANGJU_YOUTH_URL),
))
def test_live_two_stable_complete_owner_censuses(owner: str, provider: str, url: str) -> None:
    snapshots = []
    for _ in range(2):
        rows, _, meta = gj.collect_gyeonggi_gwangju_education_courses(
            {"provider": provider, "url": url},
            today="2026-07-23",
            allow_raw_requests_for_tests=True,
        )
        assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
        assert meta["pagination_complete"] and meta["details_complete"]
        assert meta["application_endpoints_called"] == 0
        assert len(rows) == meta["returned_count"] == meta["current_count"]
        assert all(row["municipality_code"] == "4161000000" for row in rows)
        assert all("instructor" not in row and "phone" not in row for row in rows)
        snapshots.append((meta["source_total"], meta["current_count"], meta["source_identity_sha256"]))
    assert snapshots[0] == snapshots[1]
    baseline = gj.GYEONGGI_GWANGJU_LIVE_AUDIT_BASELINE.get(owner)
    if baseline:
        assert snapshots[0][0] == baseline["source_total"]
        assert snapshots[0][1] == baseline["current_count"]
        assert snapshots[0][2] == baseline["sorted_identity_sha256"]
