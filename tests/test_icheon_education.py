from __future__ import annotations

from dataclasses import dataclass
from html import escape
import os
from typing import Any, Callable

import pytest

from Crawler import municipal_icheon as icheon


@dataclass
class Target:
    provider: str
    url: str


class Response:
    def __init__(self, url: str, *, text: str = "", payload: Any = None, status: int = 200) -> None:
        self.url = url
        self.text = text
        self.content = text.encode("utf-8")
        self._payload = payload
        self.status_code = status
        self.history: list[Any] = []

    def json(self) -> Any:
        return self._payload


class FakeSession:
    def __init__(
        self,
        get_route: Callable[[str, int], Response],
        post_route: Callable[[str, dict[str, Any], int], Response] | None = None,
    ) -> None:
        self.headers: dict[str, str] = {}
        self.get_route = get_route
        self.post_route = post_route
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.counts: dict[tuple[str, str], int] = {}

    def get(self, url: str, **kwargs: Any) -> Response:
        self.calls.append(("GET", url, kwargs))
        key = ("GET", url)
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.get_route(url, self.counts[key])

    def post(self, url: str, **kwargs: Any) -> Response:
        self.calls.append(("POST", url, kwargs))
        key = ("POST", url)
        self.counts[key] = self.counts.get(key, 0) + 1
        if self.post_route is None:
            raise AssertionError(f"unexpected POST {url}")
        return self.post_route(url, kwargs, self.counts[key])

    def close(self) -> None:
        return None


def _target(provider: str, url: str) -> Target:
    return Target(provider=provider, url=url)


def test_owner_ids_and_candidate_hashes_are_stable() -> None:
    assert icheon.ICHEON_CITY_CODE == "4150000000"
    assert icheon.ICHEON_CITY_PROVIDER == "MUNI_WWW_ICHEON_GO_KR_1B4316ED"
    assert icheon.ICHEON_GSEEK_PROVIDER == "MUNI_ICHEON_GSEEK_KR_18B68AC1"
    assert icheon.ICHEON_GSEEK_CANDIDATE_ID == "MUNI_IR_FB96DA9F85D7"
    assert icheon.ICHEON_LIBRARY_PROVIDER == "MUNI_WWW_ICHEONLIB_GO_KR_76E3CE6D"
    assert icheon.ICHEON_LIBRARY_CANDIDATE_ID == "MUNI_IR_1227B4EA45D5"
    assert icheon.ICHEON_ARTIC_PROVIDER == "MUNI_WWW_ARTIC_OR_KR_9B6E3C8E"
    assert icheon.ICHEON_ARTIC_CANDIDATE_ID == "MUNI_IR_F711ABF92A5A"


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        (icheon.ICHEON_CITY_PROVIDER, icheon.ICHEON_CITY_URL),
        (icheon.ICHEON_GSEEK_PROVIDER, icheon.ICHEON_GSEEK_URL),
        (icheon.ICHEON_LIBRARY_PROVIDER, icheon.ICHEON_LIBRARY_URL),
        (icheon.ICHEON_ARTIC_PROVIDER, icheon.ICHEON_ARTIC_URL),
    ],
)
def test_only_exact_owner_targets_are_accepted(provider: str, url: str) -> None:
    assert icheon.is_target(_target(provider, url))
    assert not icheon.is_target(_target(provider, url + "&unexpected=1"))
    assert not icheon.is_target(_target(provider + "_OTHER", url))


@pytest.mark.parametrize(
    "url",
    [
        "https://www.icheon.go.kr/edu/eduLctr/apply/pwd.do?mid=0101000000",
        "https://www.icheon.go.kr/edu/eduLctr/apply/write.do?mid=0101000000",
        "https://www.icheon.go.kr/file/direct/download.do",
        "https://icheon.gseek.kr/user/login",
        "https://www.artic.or.kr/base/nrr/academyReservation/artic/create",
        "https://www.icheonlib.go.kr/mylist/pop/writeitem",
    ],
)
def test_application_identity_and_attachment_routes_are_forbidden(url: str) -> None:
    with pytest.raises(icheon.IcheonContractError):
        icheon._guard_url(url)


def test_candidate_and_alias_audit_separates_owners() -> None:
    assert icheon.ICHEON_CANDIDATE_AUDIT["MUNI_IR_091C3D3C5E71"]["decision"] == (
        "unsafe_application_history_alias_retarget"
    )
    assert icheon.ICHEON_CANDIDATE_AUDIT[icheon.ICHEON_ARTIC_CANDIDATE_ID]["owner"] == (
        icheon.ICHEON_ARTIC_PROVIDER
    )
    assert any(
        item["reason"] == "exact_agency_51_subset_of_city_education_owner"
        for item in icheon.ICHEON_NON_EXECUTING_ALIASES
    )
    assert icheon.ICHEON_WORKER_WELFARE_PROVIDER == "ICHEON_WORKER_WELFARE"


def _city_filters() -> str:
    agencies = [
        ("16", "농업기술센터"),
        ("17", "여성회관"),
        ("40", "이천시보건소"),
        ("43", "이천시 농업정책과"),
        ("45", "시민정보화 교육(시청 6층)"),
        ("48", "이천시보건소 보건위생과"),
        ("50", "여성보육과(이천행복학교)"),
        ("51", "이천시 청소년생활문화센터"),
        ("52", "첨단전략산업과"),
        ("999", "테스트"),
    ]
    districts = [
        "장호원읍", "부발읍", "신둔면", "백사면", "마장면", "대월면", "모가면",
        "설성면", "호법면", "율면", "창전동", "증포동", "중리동", "관고동",
    ]
    agency_html = "".join(
        f'<a data-agency-idx="{code}">{escape(name)}</a>' for code, name in agencies
    )
    district_html = "".join(
        f'<input name="searchAgency" id="district{i}" data-agency-idx="{i}" />'
        f'<label for="district{i}">{escape(name)}</label>'
        for i, name in enumerate(districts, 1)
    )
    return f'<form id="listForm">{agency_html}{district_html}</form>'


def _city_tr(
    identity: int,
    *,
    title: str,
    branch: str,
    end: str,
    status: str,
    start: str = "2026-08-01",
) -> str:
    return f"""
      <tr>
        <td>{identity}</td>
        <td><a data-req-get-p-idx="{identity}">{escape(title)}</a><span>{escape(branch)}</span></td>
        <td>2026-07-01 ~ 2026-07-31</td>
        <td>{start} ~ {end}</td>
        <td>월</td><td>교육실</td><td>무료</td><td>{escape(status)}</td><td>2 / 20 (0 / 5)</td>
      </tr>
    """


def _city_list(rows: list[str], pages: int, *, filters: bool = False) -> str:
    return f"""
      <html><head><title>프로그램현황 목록 | 교육/강좌신청 | 이천시청 홈페이지</title></head>
      <body>{_city_filters() if filters else ''}
        <table><tbody>{''.join(rows)}</tbody></table>
        <div class="bod_page"><a class="btn_end" onclick="goPage({pages}); return false;"></a></div>
      </body></html>
    """


def _city_detail(identity: int, title: str, branch: str, status: str) -> str:
    control = ""
    form = ""
    if status == "접수중":
        form = '<form id="postListForm" action="/edu/eduLctr/apply/write.do?mid=0101000000"></form>'
        control = '<a class="btn point" data-req-form-id="postListForm">신청하기</a>'
    pairs = [
        ("교육기관", branch), ("강좌명", title), ("접수기간", "2026-07-01 ~ 2026-07-31"),
        ("교육기간", "2026-08-01 ~ 2026-08-31"), ("교육시간", "월 10:00 ~ 12:00"),
        ("교육대상", "이천시민"), ("교육장소", "교육실"), ("수강료", "무료"),
        ("모집정원", "20명 / 5명 (정원 / 대기)"), ("교육내용", "안전한 공개 설명"),
    ]
    body = "".join(f"<dl><dt>{escape(k)}</dt><dd>{escape(v)}</dd></dl>" for k, v in pairs)
    return f'<html><input name="idx" value="{identity}"/><div class="bod_write">{body}</div>{form}{control}</html>'


def _city_fixture(*, sentinel_row: bool = False, unstable: bool = False) -> FakeSession:
    page1_rows = [
        _city_tr(
            1,
            title="현재 시 강좌",
            branch="시민정보화 교육(시청 6층)",
            end="2026-08-31",
            status="접수중",
        )
    ]
    page1_rows.extend(
        _city_tr(
            identity,
            title=f"과거 강좌 {identity}",
            branch="여성회관",
            start="2025-01-01",
            end="2025-02-01",
            status="교육완료",
        )
        for identity in range(2, 13)
    )
    page2_row = _city_tr(
        13,
        title="현재 보건 강좌",
        branch="이천시보건소",
        end="2026-08-31",
        status="교육중",
    )
    page1 = _city_list(page1_rows, 2, filters=True)
    page2 = _city_list([page2_row], 2)
    sentinel = _city_list([page2_row] if sentinel_row else [], 2)

    def route(url: str, count: int) -> Response:
        if url == icheon.ICHEON_CITY_MAIN_URL:
            return Response(url, text="<html><title>교육포털</title></html>")
        if url == icheon._city_page_url(1):
            body = page1.replace("현재 시 강좌", "변경 강좌") if unstable and count > 1 else page1
            return Response(url, text=body)
        if url == icheon._city_page_url(2):
            return Response(url, text=page2)
        if url == icheon._city_page_url(3):
            return Response(url, text=sentinel)
        if url == icheon._city_detail_url(1):
            return Response(url, text=_city_detail(1, "현재 시 강좌", "시민정보화 교육(시청 6층)", "접수중"))
        if url == icheon._city_detail_url(13):
            return Response(url, text=_city_detail(13, "현재 보건 강좌", "이천시보건소", "교육중"))
        raise AssertionError(f"unexpected GET {url}")

    return FakeSession(route)


def test_city_collector_proves_full_boundary_and_all_current_details() -> None:
    fake = _city_fixture()
    rows, parser, meta = icheon.collect_icheon_city_education(
        _target(icheon.ICHEON_CITY_PROVIDER, icheon.ICHEON_CITY_URL),
        today="2026-07-23",
        max_pages=5,
        detail_limit=5,
        max_requests=20,
        session_factory=lambda: fake,
        sleeper=lambda _: None,
    )
    assert parser == icheon.ICHEON_CITY_PARSER
    assert [row["title"] for row in rows] == ["현재 시 강좌", "현재 보건 강좌"]
    assert rows[0]["branch"] == "시민정보화 교육(시청 6층)"
    assert rows[0]["collection_category"] == "공공예약"
    assert rows[0]["domain_category"] == "교육·강좌"
    assert rows[0]["source_group"] == "municipal_reservation"
    assert rows[0]["service_group_policy"] == "locked"
    assert rows[0]["application_url"] == rows[0]["raw_url"]
    assert rows[1]["reservation_available"] is False
    assert meta["source_total"] == 13
    assert meta["page_counts"] == {1: 12, 2: 1}
    assert meta["sentinel_count"] == 0
    assert meta["stability_rechecks"] == 2
    assert meta["detail_pages"] == 2
    assert meta["snapshot_complete"] is True
    assert all(not any(part in url.lower() for part in icheon._FORBIDDEN_PATH_PARTS) for _, url, _ in fake.calls)


@pytest.mark.parametrize("failure", ["sentinel", "unstable"])
def test_city_collector_fails_closed_on_boundary_drift(failure: str) -> None:
    fake = _city_fixture(sentinel_row=failure == "sentinel", unstable=failure == "unstable")
    rows, _parser, meta = icheon.collect_icheon_city_education(
        _target(icheon.ICHEON_CITY_PROVIDER, icheon.ICHEON_CITY_URL),
        today="2026-07-23",
        max_pages=5,
        detail_limit=5,
        max_requests=20,
        session_factory=lambda: fake,
        sleeper=lambda _: None,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"]


def _gseek_item(identity: int, *, current: bool, status: str) -> dict[str, Any]:
    start = "2026.08.01" if current else "2025.01.01"
    end = "2026.08.31" if current else "2025.02.01"
    return {
        "d_sbjct_sn": str(identity),
        "d_sbjct_cycl_sn": "1",
        "d_sbjct_nm": f"이천 지식 강좌 {identity}",
        "d_edu_gvmnfc": "호법면행정복지센터",
        "d_rgn": "호법면",
        "d_co_sprvsn_id": "G000009",
        "d_sbjct_type_cd_id": "OF",
        "d_recrut_stts_nm": status,
        "d_edu_bgng_dt": start,
        "d_edu_end_dt": end,
        "d_is_single_day_course": "N",
        "d_edu_start_time": "10:00",
        "d_edu_end_time": "12:00",
        "d_edu_wday_cd_nm": "월",
        "d_clsf_depth1_nm": "취미·건강",
        "d_clsf_depth2_nm": "생활",
        "d_clsf_depth3_nm": "생활",
        "d_edu_nope": "20",
        "d_aply_cnt": "2",
        "d_sbjct_amt": "0",
        "d_prepar_cmdty_amt": "0",
        "d_sbjct_trgt_nm_1": "성인",
        "d_stdnt_chice_mthd_cd_nm": "선착",
        "d_sbjct_intrd_cn": "공개 강좌 설명",
        "d_total_cnt": "10",
    }


def _gseek_landing() -> str:
    return """
      <html><head><title>이천시 평생학습포털</title></head><body>
      <input id="s_resion_cd1" name="s_resion_cd1" value="4150000000" />
      <input name="ARK_CO_SPRVSN_ID" value="G000009" />
      <p>총 10개의 강좌</p><script>/user/course/offline/list/search</script>
      </body></html>
    """


def _gseek_detail(item: dict[str, Any], *, mismatch: bool = False) -> str:
    subject = item["d_sbjct_sn"]
    title = "다른 제목" if mismatch else item["d_sbjct_nm"]
    status = item["d_recrut_stts_nm"]
    control = '<a class="btn-course-apply" onclick="fnAply(); return false;">수강신청</a>' if status == "모집중" else ""
    return f"""
      <html><input name="s_sbjct_sn" value="{subject}"/><input name="s_sbjct_cycl_sn" value="1"/>
      <div class="course-detail-container">
        <section class="key-course-info"><span class="tag-field">호법면행정복지센터</span>
          <span class="tag-type offline-type">호법면</span><span class="tag-item-xs">{status}</span></section>
        <h2 class="course-title">{escape(title)}</h2>
        <dl><dt>신청기간</dt><dd>2026.07.01 ~ 2026.07.31</dd>
          <dt>학습기간</dt><dd>2026.08.01 ~ 2026.08.31</dd>
          <dt>교육시간</dt><dd>10:00 ~ 12:00</dd><dt>교육대상</dt><dd>성인</dd>
          <dt>수강료</dt><dd>무료</dd><dt>재료비</dt><dd>없음</dd>
          <dt>교육장소</dt><dd>호법복지관 교육실</dd></dl>{control}
      </div></html>
    """


def _gseek_fixture(*, sentinel_row: bool = False, detail_mismatch: bool = False) -> FakeSession:
    items = [_gseek_item(1, current=True, status="모집중")]
    items.extend(_gseek_item(i, current=False, status="마감") for i in range(2, 10))
    items.append(_gseek_item(10, current=True, status="마감"))

    def get_route(url: str, _count: int) -> Response:
        if url == icheon.ICHEON_GSEEK_URL:
            return Response(url, text=_gseek_landing())
        for item in (items[0], items[-1]):
            if url == icheon._gseek_detail_url(item["d_sbjct_sn"], "1"):
                return Response(url, text=_gseek_detail(item, mismatch=detail_mismatch))
        raise AssertionError(f"unexpected GET {url}")

    def post_route(url: str, kwargs: dict[str, Any], _count: int) -> Response:
        assert url == icheon.ICHEON_GSEEK_API_URL
        data = kwargs["data"]
        assert data["resion"] == "4150000000"
        start = int(data["s_row_start"])
        payload = items[:9] if start == 1 else items[9:] if start == 10 else [items[-1]] if sentinel_row else []
        return Response(url, payload=payload)

    return FakeSession(get_route, post_route)


def test_gseek_collector_partitions_parent_and_validates_all_current_details() -> None:
    fake = _gseek_fixture()
    rows, parser, meta = icheon.collect_icheon_gseek_education(
        _target(icheon.ICHEON_GSEEK_PROVIDER, icheon.ICHEON_GSEEK_URL),
        today="2026-07-23",
        max_pages=5,
        detail_limit=5,
        max_requests=20,
        session_factory=lambda: fake,
        sleeper=lambda _: None,
    )
    assert parser == icheon.ICHEON_GSEEK_PARSER
    assert [row["title"] for row in rows] == ["이천 지식 강좌 1", "이천 지식 강좌 10"]
    assert rows[0]["reservation_available"] is True
    assert rows[1]["reservation_available"] is False
    assert meta["source_total"] == 10
    assert meta["page_counts"] == {1: 9, 2: 1, 3: 0}
    assert meta["parent_aggregate_provider"] == "GYEONGGI_GSEEK"
    assert meta["parent_exclusion_required"] == "G000009"
    assert meta["details_complete"] is True
    assert all(
        method != "POST" or url == icheon.ICHEON_GSEEK_API_URL
        for method, url, _kwargs in fake.calls
    )


@pytest.mark.parametrize("failure", ["sentinel", "detail"])
def test_gseek_collector_fails_closed_on_contract_drift(failure: str) -> None:
    fake = _gseek_fixture(
        sentinel_row=failure == "sentinel",
        detail_mismatch=failure == "detail",
    )
    rows, _parser, meta = icheon.collect_icheon_gseek_education(
        _target(icheon.ICHEON_GSEEK_PROVIDER, icheon.ICHEON_GSEEK_URL),
        today="2026-07-23",
        max_pages=5,
        detail_limit=5,
        max_requests=20,
        session_factory=lambda: fake,
        sleeper=lambda _: None,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"]


def _library_options() -> str:
    labels = {
        "MA000000": "시립도서관", "BR000000": "청미도서관", "MC000000": "효양도서관",
        "MB000000": "어린이도서관", "NA000000": "마장도서관", "NB000000": "서희도서관",
    }
    return "".join(f'<option value="{code}">{name}</option>' for code, name in labels.items())


def _library_li(
    identity: int,
    *,
    title: str,
    branch_label: str,
    start: str,
    end: str,
    status: str,
) -> str:
    return f"""
      <li><a href="/education/detail?lecture_id={identity}"><h3 class="eventTitle">
        <span>{escape(branch_label)}</span>{escape(title)}</h3></a>
        <div class="eventList2">
          <dl><dt>강좌기간</dt><dd>{start} ~ {end} 월 10:00 ~ 12:00</dd></dl>
          <dl><dt>접수기간</dt><dd>2026-07-01 ~ 2026-07-31</dd></dl>
          <dl><dt>강좌대상</dt><dd>이천시민</dd></dl><dl><dt>강좌장소</dt><dd>문화교실</dd></dl>
        </div><div class="eventBtn"><ul class="numList"><li>정원<span>10</span>/</li>
          <li>신청<span>2</span></li></ul><a class="eventBtnStyle">{escape(status)}</a></div>
      </li>
    """


def _library_list(rows: list[str], total: int = 11, pages: int = 2) -> str:
    return f"""
      <html><head><title>이용자교육 목록 | 이천시 통합 도서관</title></head><body>
      <form id="search"><select name="loca">{_library_options()}</select></form>
      <span class="totalcount">{total}</span><p class="pageNum">, 1 /{pages}페이지</p>
      <div class="eventList"><ul>{''.join(rows)}</ul></div></body></html>
    """


def _library_detail(identity: int, title: str, branch: str, status: str) -> str:
    return f"""
      <html><dl><dt><a href="javascript:;">[{escape(branch)}] {escape(title)}</a></dt>
      <dd><span>강좌기간 :</span> 2026-08-01 ~ 2026-08-31</dd>
      <dd><span>시간 :</span> 월 10:00 ~ 12:00</dd>
      <dd><span>접수기간 :</span> 2026-07-01 ~ 2026-07-31</dd>
      <dd><span>강좌장소 :</span> 문화교실</dd><dd><span>신청인원 :</span> 2명 / 10명</dd>
      <dd><span>대상 :</span> 이천시민</dd><dd><span>상태 :</span> {escape(status)}</dd></dl></html>
    """


def _library_fixture(*, sentinel_row: bool = False) -> FakeSession:
    current1 = _library_li(
        1, title="현재 독서 강좌", branch_label="시립도서관",
        start="2026-08-01", end="2026-08-31", status="접수중",
    )
    expired = [
        _library_li(
            i, title=f"과거 독서 강좌 {i}", branch_label="청미도서관",
            start="2025-01-01", end="2025-02-01", status="접수마감",
        )
        for i in range(2, 11)
    ]
    current2 = _library_li(
        11, title="현재 어린이 강좌", branch_label="어린이도서관",
        start="2026-08-01", end="2026-08-31", status="접수 전",
    )
    page1 = _library_list([current1, *expired])
    page2 = _library_list([current2])
    sentinel = _library_list([current2] if sentinel_row else [])

    def route(url: str, _count: int) -> Response:
        if url == icheon.ICHEON_LIBRARY_URL:
            return Response(url, text=page1)
        if url == icheon._library_page_url(2):
            return Response(url, text=page2)
        if url == icheon._library_page_url(3):
            return Response(url, text=sentinel)
        if url == icheon._library_detail_url(1):
            return Response(url, text=_library_detail(1, "현재 독서 강좌", "이천시립도서관", "접수중"))
        if url == icheon._library_detail_url(11):
            return Response(url, text=_library_detail(11, "현재 어린이 강좌", "이천시립어린이도서관", "접수 전"))
        raise AssertionError(f"unexpected GET {url}")

    return FakeSession(route)


def test_library_collector_uses_six_official_branches_and_exact_boundary() -> None:
    fake = _library_fixture()
    rows, parser, meta = icheon.collect_icheon_library_education(
        _target(icheon.ICHEON_LIBRARY_PROVIDER, icheon.ICHEON_LIBRARY_URL),
        today="2026-07-23",
        max_pages=5,
        detail_limit=5,
        max_requests=20,
        session_factory=lambda: fake,
        sleeper=lambda _: None,
    )
    assert parser == icheon.ICHEON_LIBRARY_PARSER
    assert [row["branch"] for row in rows] == ["이천시립도서관", "이천시립어린이도서관"]
    assert rows[0]["capacity_current"] == 2
    assert rows[0]["capacity_total"] == 10
    assert rows[1]["status"] == "SCHEDULED"
    assert meta["source_total"] == 11
    assert meta["page_counts"] == {1: 10, 2: 1}
    assert meta["sentinel_count"] == 0
    assert meta["details_complete"] is True


def test_library_collector_fails_closed_on_nonempty_sentinel() -> None:
    fake = _library_fixture(sentinel_row=True)
    rows, _parser, meta = icheon.collect_icheon_library_education(
        _target(icheon.ICHEON_LIBRARY_PROVIDER, icheon.ICHEON_LIBRARY_URL),
        today="2026-07-23",
        max_pages=5,
        detail_limit=5,
        max_requests=20,
        session_factory=lambda: fake,
        sleeper=lambda _: None,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False


def _artic_card(identity: int, title: str, end: str, status: str) -> str:
    return f"""
      <div class="academy_list_wrap"><div class="academy_list_left">
      <span class="academy_list_left_tag_state">{escape(status)}</span>
      <div class="academy_list_left_title"><h2>{escape(title)}</h2></div>
      <ul class="academy_list_left_info_box">
        <li><strong>교육기간</strong><div class="academy_list_left_info_box_item_result">20260801 ~ {end}</div></li>
        <li><strong>시간</strong><div class="academy_list_left_info_box_item_result">월 10:00 ~ 12:00</div></li>
        <li><strong>수강료</strong><div class="academy_list_left_info_box_item_result">30,000 원</div></li>
        <li><strong>정원</strong><div class="academy_list_left_info_box_item_result">2 / 15명</div></li>
      </ul></div><a class="academy_list_right_more"
      href="https://www.artic.or.kr:443/base/nrr/academy/artic/read?academyNo={identity}&amp;menuLevel=2&amp;menuNo=13">상세보기</a></div>
    """


def _artic_list(cards: list[str], pages: int = 1) -> str:
    links = "".join(f'<a class="active">{page} <span>페이지</span></a>' for page in range(1, pages + 1))
    return f'<html><head><title>강좌안내 및 수강신청 | 아카데미 | 이천문화재단</title></head><body>{"".join(cards)}<div class="pagination">{links}</div></body></html>'


def _artic_detail(identity: int, title: str) -> str:
    return f"""
      <html><div class="academy_view"><span class="academy_view_tag_state">마감</span>
      <div class="academy_view_title"><h2>{escape(title)}</h2></div>
      <ul class="academy_view_info_box">
        <li><strong>교육기간</strong><div class="academy_view_info_box_item_result">20260801 ~ 20260831</div></li>
        <li><strong>교재</strong><div class="academy_view_info_box_item_result"></div></li>
        <li><strong>교육시간</strong><div class="academy_view_info_box_item_result">월 10:00 ~ 12:00</div></li>
        <li><strong>참가대상</strong><div class="academy_view_info_box_item_result">이천시민</div></li>
        <li><strong>장소</strong><div class="academy_view_info_box_item_result">이천문화재단 연습실</div></li>
        <li><strong>정원</strong><div class="academy_view_info_box_item_result">15</div></li>
        <li><strong>문의접수</strong><div class="academy_view_info_box_item_result">저장 금지 010-0000-0000</div></li>
        <li><strong>수강료</strong><div class="academy_view_info_box_item_result">30,000 원</div></li>
      </ul></div></html>
    """


def _artic_fixture() -> FakeSession:
    title = "[정규 예술아카데미] 미술"
    page1 = _artic_list([_artic_card(1, title, "20260831", "마감")])
    sentinel = _artic_list([], pages=0)

    def route(url: str, _count: int) -> Response:
        if url == icheon.ICHEON_ARTIC_URL:
            return Response(url, text=page1)
        if url == icheon._artic_page_url(2):
            return Response(url, text=sentinel)
        if url == icheon._artic_detail_url(1):
            return Response(url, text=_artic_detail(1, title))
        raise AssertionError(f"unexpected GET {url}")

    return FakeSession(route)


def test_artic_collector_validates_current_detail_without_retaining_contact() -> None:
    fake = _artic_fixture()
    rows, parser, meta = icheon.collect_icheon_artic_education(
        _target(icheon.ICHEON_ARTIC_PROVIDER, icheon.ICHEON_ARTIC_URL),
        today="2026-07-23",
        max_pages=3,
        detail_limit=3,
        max_requests=10,
        session_factory=lambda: fake,
        sleeper=lambda _: None,
    )
    assert parser == icheon.ICHEON_ARTIC_PARSER
    assert len(rows) == 1
    assert rows[0]["branch"] == "이천문화재단"
    assert rows[0]["venue_name"] == "이천문화재단 연습실"
    assert "phone" not in rows[0]
    assert "문의접수" not in rows[0]["raw_fields"]
    assert meta["snapshot_complete"] is True
    assert meta["sentinel_count"] == 0


def test_pii_description_is_replaced_by_title() -> None:
    description, redacted = icheon._public_description("문의 010-1234-5678", "안전한 제목")
    assert description == "안전한 제목"
    assert redacted is True


def test_cross_owner_overlap_is_report_only_and_worker_owner_stays_separate() -> None:
    shared = {"title": "같은 강좌", "start_date": "2026-08-01", "end_date": "2026-08-31"}
    audit = icheon.icheon_cross_owner_overlap(
        {
            icheon.ICHEON_CITY_PROVIDER: [{**shared, "provider_course_id": "city:1"}],
            icheon.ICHEON_LIBRARY_PROVIDER: [{**shared, "provider_course_id": "library:1"}],
        }
    )
    assert audit["overlap_count"] == 1
    assert audit["worker_welfare_merged"] is False
    assert audit["gseek_parent_exclusion_required"] == "G000009"


def test_dispatcher_rejects_non_owner_alias_without_network() -> None:
    rows, parser, meta = icheon.collect_icheon_education_courses(
        _target("ICHEON_WORKER_WELFARE", icheon.ICHEON_WORKER_WELFARE_URL)
    )
    assert rows == []
    assert parser == "icheon_owner_dispatch"
    assert "unknown Icheon provider" in meta["configured_collection_error"]
    assert meta["full_snapshot_validated"] is False
    assert meta["application_endpoints_called"] == 0


def test_dispatcher_adds_shared_complete_snapshot_metadata() -> None:
    fake = _artic_fixture()
    rows, _parser, meta = icheon.collect_icheon_education_courses(
        _target(icheon.ICHEON_ARTIC_PROVIDER, icheon.ICHEON_ARTIC_URL),
        today="2026-07-23",
        max_pages=3,
        detail_limit=3,
        max_requests=10,
        session_factory=lambda: fake,
        sleeper=lambda _: None,
    )
    assert len(rows) == 1
    assert meta["discovered_links"] == 1
    assert meta["pagination_detected"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["no_current_reason"] == ""
    assert meta["application_endpoints_called"] == 0


@pytest.mark.skipif(os.getenv("ICHEON_LIVE_TESTS") != "1", reason="opt-in live source contract")
def test_live_two_run_owner_stability() -> None:
    cases = [
        (icheon.ICHEON_CITY_PROVIDER, icheon.ICHEON_CITY_URL),
        (icheon.ICHEON_GSEEK_PROVIDER, icheon.ICHEON_GSEEK_URL),
        (icheon.ICHEON_LIBRARY_PROVIDER, icheon.ICHEON_LIBRARY_URL),
        (icheon.ICHEON_ARTIC_PROVIDER, icheon.ICHEON_ARTIC_URL),
    ]
    for provider, url in cases:
        signatures = []
        for _ in range(2):
            rows, _parser, meta = icheon.collect_icheon_education_courses(
                _target(provider, url),
                today="2026-07-23",
                max_pages=450,
                detail_limit=500,
                max_requests=1200,
                allow_raw_requests_for_tests=True,
            )
            assert meta["snapshot_complete"] is True
            signatures.append(
                icheon._signature(
                    (row["provider_course_id"], row["title"], row["end_date"]) for row in rows
                )
            )
        assert signatures[0] == signatures[1]
