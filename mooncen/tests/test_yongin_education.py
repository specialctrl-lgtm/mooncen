from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
import os
from typing import Any, Callable

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_yongin as yongin


@dataclass
class Target:
    provider: str
    url: str


class Response:
    def __init__(
        self,
        url: str,
        *,
        text: str = "",
        payload: Any = None,
        status: int = 200,
        encoding: str = "utf-8",
    ) -> None:
        self.url = url
        self.text = text
        self.content = text.encode(encoding)
        self.encoding = encoding
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


def _assert_locked_classification(
    row: dict[str, Any], *, domain: str = "교육·강좌", service_group: str = "공공강좌"
) -> None:
    assert row["collection_category"] == "공공예약"
    assert row["domain_category"] == domain
    assert row["source_group"] == "municipal_reservation"
    assert row["service_group"] == service_group
    assert row["service_group_policy"] == "locked"
    assert row["classification_locked"] is True


OWNER_PAIRS = [
    (yongin.YONGIN_RESERVATION_PROVIDER, yongin.YONGIN_RESERVATION_URL),
    (yongin.YONGIN_CHEOIN_PROVIDER, yongin.YONGIN_CHEOIN_URL),
    (yongin.YONGIN_GIHEUNG_PROVIDER, yongin.YONGIN_GIHEUNG_URL),
    (yongin.YONGIN_SUJI_PROVIDER, yongin.YONGIN_SUJI_URL),
    (yongin.YONGIN_LIBRARY_PROVIDER, yongin.YONGIN_LIBRARY_URL),
    (yongin.YONGIN_YICF_PROVIDER, yongin.YONGIN_YICF_URL),
    (yongin.YONGIN_ONECLICK_PROVIDER, yongin.YONGIN_ONECLICK_URL),
    (yongin.YONGIN_YIYF_COURSE_PROVIDER, yongin.YONGIN_YIYF_COURSE_URL),
]


def test_owner_ids_candidate_ids_and_city_code_are_stable() -> None:
    assert yongin.YONGIN_CITY_CODE == "4146000000"
    assert [provider for provider, _ in OWNER_PAIRS] == [
        "MUNI_RESVE_YONGIN_GO_KR_221336AC",
        "MUNI_JACHI_YONGIN_GO_KR_10340408",
        "MUNI_JACHI_YONGIN_GO_KR_60025DB9",
        "MUNI_JACHI_YONGIN_GO_KR_91C5118C",
        "MUNI_LIB_YONGIN_GO_KR_B7626320",
        "MUNI_WWW_YICF_OR_KR_B2E137D5",
        "MUNI_YIYF_OR_KR_F56DFD54",
        "MUNI_SPORTS_YIYF_OR_KR_206DDBA6",
    ]
    assert [
        yongin.YONGIN_RESERVATION_CANDIDATE_ID,
        yongin.YONGIN_CHEOIN_CANDIDATE_ID,
        yongin.YONGIN_GIHEUNG_CANDIDATE_ID,
        yongin.YONGIN_SUJI_CANDIDATE_ID,
        yongin.YONGIN_LIBRARY_CANDIDATE_ID,
        yongin.YONGIN_YICF_CANDIDATE_ID,
        yongin.YONGIN_ONECLICK_CANDIDATE_ID,
        yongin.YONGIN_YIYF_COURSE_CANDIDATE_ID,
    ] == [
        "MUNI_IR_45E5AF9C5003",
        "MUNI_IR_45CDD3249830",
        "MUNI_IR_EFE3A66475B8",
        "MUNI_IR_1F29A18E4FBC",
        "MUNI_IR_6409719F2B72",
        "MUNI_IR_0676AF94DDA1",
        "MUNI_IR_EFC7E0BCAC38",
        "MUNI_IR_612302071933",
    ]


@pytest.mark.parametrize(("provider", "url"), OWNER_PAIRS)
def test_only_exact_owner_targets_are_accepted(provider: str, url: str) -> None:
    assert yongin.is_target(_target(provider, url))
    assert not yongin.is_target(_target(provider + "_OTHER", url))
    assert not yongin.is_target(_target(provider, url + "&unexpected=1"))


def test_official_jachi_partitions_are_exact_and_disjoint() -> None:
    branches = yongin.YONGIN_JACHI_BRANCHES
    assert [len(branches[key]) for key in ("cheoin", "giheung", "suji")] == [12, 13, 9]
    code_sets = [set(branches[key]) for key in ("cheoin", "giheung", "suji")]
    assert len(set.union(*code_sets)) == 34
    assert not (code_sets[0] & code_sets[1] or code_sets[0] & code_sets[2] or code_sets[1] & code_sets[2])
    assert branches["cheoin"]["YIJM33"] == "삼가동"
    assert branches["giheung"]["YIJM32"] == "동백3동"
    assert branches["giheung"]["YIJM34"] == "보라동"
    assert branches["suji"]["YIJM23"] == "풍덕천1동 주민자치센터"
    assert yongin._jachi_expected_companies() == {
        yongin.YONGIN_JACHI_ADMIN_COMPANY,
        *(item for group in branches.values() for item in group.items()),
    }


def test_library_and_youth_branch_directories_are_exact() -> None:
    assert len(yongin.YONGIN_LIBRARY_BRANCHES) == 22
    assert yongin.YONGIN_LIBRARY_BRANCHES["CE"] == "도서관정책과"
    assert yongin.YONGIN_LIBRARY_BRANCHES["MX"] == "이동꿈틀도서관"
    assert len(yongin.YONGIN_YIYF_EDUCATION_BRANCHES) == 9
    assert len(yongin.YONGIN_YIYF_SPORTS_BRANCHES) == 2
    assert yongin.YONGIN_YIYF_SPORTS_BRANCHES == {
        "10003": "용인청소년수련관(체육)",
        "10013": "용천초어울림센터",
    }


@pytest.mark.parametrize(
    ("url", "method"),
    [
        ("https://jachi.yongin.go.kr/cheoingu/79?action=write", "GET"),
        ("https://jachi.yongin.go.kr/rest/lecture/family_list", "GET"),
        ("https://jachi.yongin.go.kr/rest/lecture/list_reregistration", "GET"),
        ("https://sports.yiyf.or.kr/main_new/m03/m03_insert_all.asp", "POST"),
        ("https://yiyf.or.kr/member/login", "GET"),
        ("https://lib.yongin.go.kr/file/direct/download", "GET"),
        ("https://resve.yongin.go.kr/resve/manage/cancel.do", "GET"),
    ],
)
def test_application_identity_and_attachment_routes_are_forbidden(url: str, method: str) -> None:
    with pytest.raises(yongin.YonginContractError):
        yongin._guard_url(url, method)


def test_only_two_public_jachi_posts_are_allowed() -> None:
    yongin._guard_url(yongin.YONGIN_JACHI_API_URL, "POST")
    yongin._guard_url(yongin.YONGIN_JACHI_COMPANY_URL, "POST")
    with pytest.raises(yongin.YonginContractError):
        yongin._guard_url(yongin.YONGIN_RESERVATION_URL, "POST")


def test_literal_response_url_and_euckr_decode_regressions() -> None:
    url = yongin.YONGIN_LIBRARY_URL + "&currentPageNo=2"
    yongin._validate_response(Response(url, text="ok"), url)
    response = Response(
        yongin.YONGIN_YIYF_COURSE_URL,
        text="<html><body>수지청소년문화의집</body></html>",
        encoding="cp949",
    )
    response.encoding = "euc-kr"
    soup = yongin._response_soup(response, yongin.YONGIN_YIYF_COURSE_URL)
    assert "수지청소년문화의집" in soup.get_text()


def _resve_card(category: str, identity: int, title: str, branch: str) -> str:
    return f"""
      <li><div class="service-receiving">접수중</div>
        <a href="#none" onclick="fnView({identity});">
          <div class="service-center">{escape(branch)}</div>
          <div class="service-title">{escape(title)}</div>
          <ul><li>기간</li><li>2026.08.01 ~ 2026.08.31</li></ul>
          <ul><li>인원</li><li>20명</li></ul>
          <ul><li>대상</li><li>용인시민</li></ul>
          <ul><li>장소</li><li>교육실</li></ul>
        </a>
      </li>
    """


def _resve_list(body: str) -> str:
    return f'<html><ul class="reservation-list">{body}</ul></html>'


def _resve_detail(title: str, venue: str) -> str:
    pairs = [
        ("프로그램명", title),
        ("프로그램기간", "2026.08.01 ~ 2026.08.31"),
        ("접수기간", "2026.07.01 ~ 2026.07.31"),
        ("이용대상", "용인시민"),
        ("이용료", "무료"),
        ("모집정원", "20명"),
        ("장소정보", venue),
        ("신청방법", "온라인"),
    ]
    return "<html>" + title + "<table>" + "".join(
        f"<tr><th>{escape(key)}</th><td>{escape(value)}</td></tr>" for key, value in pairs
    ) + "</table></html>"


def _reservation_fixture(
    *,
    sentinel_has_row: bool = False,
    unstable: bool = False,
    disabled_card: bool = False,
    non_programme_cards: bool = False,
) -> FakeSession:
    titles = {"CL_01": "안전체험", "CL_02": "시민교육"}
    identities = {"CL_01": 101, "CL_02": 201}

    def route(url: str, count: int) -> Response:
        for category in yongin.YONGIN_RESERVATION_CATEGORIES:
            if url == yongin._resve_page_url(category, 1):
                title = titles[category] + (" 변경" if unstable and count > 1 else "")
                body = _resve_card(category, identities[category], title, f"{category} 지점")
                if disabled_card and category == "CL_01":
                    body += """
                      <li><div class="service-before">접수전</div><a href="#none">
                        <div class="service-center">목록 전용 기관</div>
                        <div class="service-title">날짜 없는 목록 전용 프로그램</div>
                        <ul><li>기간</li><li> ~ </li></ul>
                        <ul><li>인원</li><li>20명</li></ul>
                        <ul><li>대상</li><li>전체</li></ul>
                        <ul><li>장소</li><li>목록 전용 장소</li></ul>
                      </a></li>
                    """
                if non_programme_cards and category == "CL_01":
                    body += "".join(
                        (
                            _resve_card(category, 111, "(테스트) 예약", "공원이용프로그램"),
                            _resve_card(
                                category,
                                112,
                                "생태체험프로그램 예약 안내",
                                "공원이용프로그램",
                            ),
                            _resve_card(
                                category,
                                113,
                                "찾아가는 VR 안전교육",
                                "생애주기별 찾아가는 안전교육",
                            ),
                            _resve_card(
                                category,
                                114,
                                "9월 문화관광해설사 예약",
                                "문화관광해설사 예약",
                            ),
                            _resve_card(
                                category,
                                115,
                                "찾아가는 세무 상담 신청",
                                "찾아가는 마을세무사",
                            ),
                        )
                    )
                return Response(url, text=_resve_list(body))
            if any(
                url == yongin._resve_page_url(category, page)
                for page in range(2, 7)
            ):
                body = (
                    _resve_card(category, identities[category] + 1, "경계 초과", "오류 지점")
                    if sentinel_has_row
                    else "<li>등록된 예약 프로그램이 없습니다.</li>"
                )
                return Response(url, text=_resve_list(body))
            if url == yongin._resve_detail_url(category, str(identities[category])):
                venue = (
                    "경기도 용인시 처인구 중부대로 1199"
                    if category == "CL_01"
                    else "경기도 용인시 기흥구 공세로 1"
                )
                return Response(url, text=_resve_detail(titles[category], venue))
        raise AssertionError(f"unexpected GET {url}")

    return FakeSession(route)


def test_reservation_collector_proves_two_categories_sentinels_and_details() -> None:
    fake = _reservation_fixture()
    rows, parser, meta = yongin.collect_yongin_reservation_courses(
        _target(yongin.YONGIN_RESERVATION_PROVIDER, yongin.YONGIN_RESERVATION_URL),
        max_pages=6,
        detail_limit=3,
        max_requests=20,
        detail_workers=1,
        session_factory=lambda: fake,
        sleeper=lambda _: None,
    )
    assert parser == yongin.YONGIN_RESERVATION_PARSER
    _assert_locked_classification(rows[0], domain="체험·견학", service_group="체험")
    _assert_locked_classification(rows[1])
    assert meta["source_total"] == 2
    assert meta["page_counts"] == {"CL_01": {1: 1, 2: 0}, "CL_02": {1: 1, 2: 0}}
    assert meta["detail_pages"] == 2
    assert meta["stability_rechecks"] == 4
    assert all(row["period"] for row in rows)
    assert all(row["schedule_raw"] for row in rows)
    assert all(row["fee"] for row in rows)
    assert [row["municipality_code"] for row in rows] == [
        "4146100000",
        "4146300000",
    ]
    assert all(row["venue_address"].startswith("경기도 용인시") for row in rows)
    assert all(method == "GET" for method, _, _ in fake.calls)


def test_reservation_collector_excludes_non_programme_and_unfixed_venue_cards() -> None:
    fake = _reservation_fixture(non_programme_cards=True)
    rows, _, meta = yongin.collect_yongin_reservation_courses(
        _target(yongin.YONGIN_RESERVATION_PROVIDER, yongin.YONGIN_RESERVATION_URL),
        today="2026-08-05",
        max_pages=6,
        detail_limit=3,
        max_requests=20,
        detail_workers=1,
        session_factory=lambda: fake,
        sleeper=lambda _: None,
    )

    assert [row["title"] for row in rows] == ["안전체험", "시민교육"]
    assert meta["source_total"] == 7
    assert meta["excluded_non_programme_count"] == 5
    assert meta["excluded_non_programme_reason_counts"] == {
        "mobile_programme_without_fixed_venue": 1,
        "non_programme_consultation": 1,
        "reservation_notice_shell": 1,
        "test_card": 1,
        "tour_destination_not_structured": 1,
    }
    assert meta["detail_pages"] == 2
    assert all("q_rsn=11" not in url for _method, url, _data in fake.calls)


def test_reservation_venue_requires_one_exact_yongin_district() -> None:
    assert yongin._resve_venue_municipality(
        "경기도 용인시 수지구 수지로 253"
    ) == ("4146500000", "경기도 용인시 수지구")
    with pytest.raises(yongin.YonginContractError, match="exact Yongin district"):
        yongin._resve_venue_municipality("경기도 용인시 공원")
    with pytest.raises(yongin.YonginContractError, match="exact Yongin district"):
        yongin._resve_venue_municipality("처인구와 기흥구 순회")


def test_reservation_collector_audits_but_excludes_undated_disabled_cards() -> None:
    fake = _reservation_fixture(disabled_card=True)
    rows, _, meta = yongin.collect_yongin_reservation_courses(
        _target(yongin.YONGIN_RESERVATION_PROVIDER, yongin.YONGIN_RESERVATION_URL),
        max_pages=6,
        detail_limit=3,
        max_requests=20,
        detail_workers=1,
        session_factory=lambda: fake,
        sleeper=lambda _: None,
    )

    assert len(rows) == 2
    assert meta["source_total"] == 3
    assert meta["schema_ineligible_disabled_cards"] == 1
    assert all(row.get("start_date") and row.get("end_date") for row in rows)


@pytest.mark.parametrize(
    "fixture, error",
    [
        (_reservation_fixture(sentinel_has_row=True), "no exact empty sentinel"),
        (_reservation_fixture(unstable=True), "edge changed"),
    ],
)
def test_reservation_collector_fails_closed_on_boundary_or_drift(
    fixture: FakeSession, error: str
) -> None:
    rows, _, meta = yongin.collect_yongin_reservation_courses(
        _target(yongin.YONGIN_RESERVATION_PROVIDER, yongin.YONGIN_RESERVATION_URL),
        max_pages=6,
        detail_limit=5,
        max_requests=20,
        detail_workers=1,
        session_factory=lambda: fixture,
        sleeper=lambda _: None,
    )
    assert rows == []
    assert error in meta["configured_collection_error"]


def _jachi_detail(title: str, branch: str, status: str = "R") -> str:
    pairs = [
        ("강좌명", title),
        ("운영센터", f"{branch} 주민자치센터"),
        ("교육장소", "강의실"),
        ("교육대상", "성인"),
        ("시간/요일", "월 10:00"),
        ("접수방식", "선착접수"),
    ]
    return f'<input name="status" value="{status}"><table>' + "".join(
        f"<tr><th>{escape(key)}</th><td>{escape(value)}</td></tr>" for key, value in pairs
    ) + "</table>"


def _jachi_fixture() -> FakeSession:
    companies = [
        {"comcd": code, "comnm": name}
        for code, name in sorted(yongin._jachi_expected_companies())
    ]

    def get_route(url: str, _: int) -> Response:
        if url == yongin.YONGIN_CHEOIN_URL:
            return Response(url, text="<html>주민자치 프로그램</html>")
        detail = yongin._jachi_detail_url(yongin.YONGIN_CHEOIN_URL, "YIJM01", "00001", "R")
        if url == detail:
            return Response(url, text=_jachi_detail("테스트 강좌", "포곡읍"))
        raise AssertionError(f"unexpected GET {url}")

    def post_route(url: str, kwargs: dict[str, Any], _: int) -> Response:
        if url == yongin.YONGIN_JACHI_COMPANY_URL:
            return Response(url, payload=companies)
        assert url == yongin.YONGIN_JACHI_API_URL
        data = kwargs["data"]
        if data["company_code"] == "YIJM01" and data["search_type"] == "R" and data["page"] == 1:
            payload = [{
                "comcd": "YIJM01", "comnm": "포곡읍", "class_cd": "00001",
                "class_nm": "테스트 강좌", "status": "R", "capa": "20",
                "reg_person": "2", "course_fee": "무료", "train_day_nm": "월",
                "train_stime": "10:00", "train_etime": "12:00", "target_age_name": "성인",
                "category1": "문화", "category2": "교양", "receive_kind": "10",
            }]
        else:
            payload = []
        return Response(url, payload=payload)

    return FakeSession(get_route, post_route)


def test_jachi_collector_proves_directory_partition_and_every_stream_sentinel() -> None:
    fake = _jachi_fixture()
    rows, parser, meta = yongin.collect_yongin_jachi_courses(
        _target(yongin.YONGIN_CHEOIN_PROVIDER, yongin.YONGIN_CHEOIN_URL),
        max_pages=30,
        detail_limit=2,
        max_requests=40,
        detail_workers=1,
        session_factory=lambda: fake,
        sleeper=lambda _: None,
    )
    assert parser == yongin.YONGIN_JACHI_PARSER
    assert len(rows) == 1 and rows[0]["branch"] == "포곡읍"
    assert rows[0]["period"] == "운영기간 별도 안내"
    assert rows[0]["schedule_raw"] == "월 10:00"
    assert rows[0]["fee"] == "무료"
    assert rows[0]["target"] == "성인"
    assert rows[0]["venue_name"] == "강의실"
    assert rows[0]["category_raw"] == "문화 > 교양"
    assert rows[0]["raw_fields"]["source_period_omitted"] is True
    _assert_locked_classification(rows[0])
    assert meta["branch_count"] == 12
    assert meta["active_branch_count"] == 1
    assert meta["official_company_count"] == 34
    assert meta["excluded_admin_company"] == "YIJM00"
    assert meta["page_counts"]["YIJM01:R"] == {1: 1, 2: 0}
    assert all(
        method != "POST" or url in {yongin.YONGIN_JACHI_API_URL, yongin.YONGIN_JACHI_COMPANY_URL}
        for method, url, _ in fake.calls
    )


def test_jachi_waiting_status_accepts_only_audited_detail_family() -> None:
    base = {
        "title": "요가", "branch": "역북동", "source_status": "W",
        "raw_fields": {},
    }
    for detail_status in ("E", "W"):
        soup = BeautifulSoup(_jachi_detail("요가", "역북동", detail_status), "lxml")
        assert yongin._merge_jachi_detail(dict(base), soup)["venue_name"] == "강의실"
    with pytest.raises(yongin.YonginContractError):
        yongin._merge_jachi_detail(dict(base), BeautifulSoup(_jachi_detail("요가", "역북동", "R"), "lxml"))


def _library_card(label: str, title: str, period: str) -> BeautifulSoup:
    label_html = f'<span class="lib">{escape(label)}</span>' if label else ""
    html = f"""
      <ul class="article-list lecture"><li>
        <a class="title" onclick="fnDetail('1')">{label_html}{escape(title)}</a>
        <div class="info-txt">
          <p>대상 : 성인 10명</p><p>교육장소 : 강의실</p>
          <p>수강기간 : {escape(period)}</p><p>접수기간 : 2026.07.01 ~ 2026.07.31</p>
        </div>
        <div class="statusBox"><span class="status">신청하기</span><span class="apply">2/10</span></div>
      </li></ul>
    """
    return BeautifulSoup(html, "lxml")


def test_library_parser_handles_official_full_label_historical_no_label_and_period_typo() -> None:
    spec = yongin._library_specs()[0]
    full = yongin._parse_library_cards(
        _library_card("도서관정책과", "정책 강좌", "2026.08.01 ~ 2026.08.02"),
        provider=yongin.YONGIN_LIBRARY_PROVIDER,
        spec=spec,
    )[0]
    historical = yongin._parse_library_cards(
        _library_card("", "성복도서관 재능기부 프로그램", "2026.09.04 ~ 2026.04.10 1회"),
        provider=yongin.YONGIN_LIBRARY_PROVIDER,
        spec=spec,
    )[0]
    assert full["branch"] == "도서관정책과"
    assert historical["branch"] == "성복도서관"
    assert historical["start_date"] == historical["end_date"] == "2026-09-04"
    assert historical["source_period_corrected"] is True
    _assert_locked_classification(full)
    _assert_locked_classification(historical)


def test_library_detail_maps_standard_required_fields() -> None:
    spec = yongin._library_specs()[0]
    row = yongin._parse_library_cards(
        _library_card(
            "도서관정책과",
            "정책 강좌",
            "2026.08.01 ~ 2026.08.02",
        ),
        provider=yongin.YONGIN_LIBRARY_PROVIDER,
        spec=spec,
    )[0]
    pairs = [
        ("수강기간", "2026.08.01 ~ 2026.08.02"),
        ("접수기간", "2026.07.01 ~ 2026.07.31"),
        ("수강시간/횟수", "토 10:00~12:00 / 2회"),
        ("대상", "성인"),
        ("교육장소", "강의실"),
        ("재료비", "무료"),
    ]
    soup = BeautifulSoup(
        """
        <div class="board-write">
          <div class="titleBox">
            <div class="title"><span class="lib">도서관정책과</span>정책 강좌</div>
            <span class="state">신청하기</span>
          </div>
          <ul>
        """
        + "".join(
            f'<li><span class="tit">{escape(key)}</span>'
            f'<span class="txt">{escape(value)}</span></li>'
            for key, value in pairs
        )
        + "</ul></div>",
        "lxml",
    )

    merged = yongin._merge_library_detail(row, soup)

    assert merged["period"] == "2026-08-01 ~ 2026-08-02"
    assert merged["apply_period"] == "2026-07-01 ~ 2026-07-31"
    assert merged["schedule_raw"] == "토 10:00~12:00 / 2회"
    assert merged["target"] == "성인"
    assert merged["fee"] == "무료"
    assert merged["venue_name"] == "강의실"
    assert merged["category_raw"] == "도서관 강좌"


def test_yiyf_parser_preserves_required_category_id_and_open_missing_period() -> None:
    spec = yongin._yiyf_specs()[1]
    html = """
      <div class="board-list"><ul><li>
        <a href="m03_01_view.asp?SiteCode=10003&s_item=115&itemid=1404&GotoPage=1">
          <div class="box1"><p>용인청소년수련관(체육)</p><span>생활체육</span></div>
          <div class="box2"><span class="title-blue">자유헬스</span><div class="etc">
            <dl><dt>강습기간</dt><dd>-</dd></dl><dl><dt>강습시간</dt><dd>월~금</dd></dl>
            <dl><dt>정원</dt><dd>100명</dd></dl><dl><dt>수강료</dt><dd>무료</dd></dl>
          </div></div><label>접수중</label>
        </a>
      </li></ul></div>
    """
    row = yongin._parse_yiyf_cards(
        BeautifulSoup(html, "lxml"), spec=spec, branch_code="10003"
    )[0]
    assert "s_item=115" in row["raw_url"]
    assert row["source_period_missing"] is True
    assert row["status"] == "OPEN"
    assert row["period"] == "일정 미정"
    assert row["schedule_raw"] == "월~금"
    assert row["fee"] == "무료"
    assert row["target"] == "대상 별도 안내"
    assert row["venue_name"] == "용인청소년수련관(체육)"
    assert row["category_raw"] == "생활체육"
    _assert_locked_classification(row)


def test_yicf_and_oneclick_parsers_emit_locked_production_classification() -> None:
    yicf_soup = BeautifulSoup(
        """
        <a class="involved-items" onclick="fn_update_detail('LEC1')">
          <div class="involved-items__cont"><strong>공생 강좌</strong>
            <small>기간 <span>2026.08.01 ~ 2026.08.02</span></small>
            <small>대상 <span>성인</span></small>
          </div><div class="involved-items__btns">신청중</div>
        </a>
        """,
        "lxml",
    )
    yicf_row = yongin._parse_yicf_cards(yicf_soup)[0]
    oneclick_soup = BeautifulSoup(
        """
        <div id="content">총 1건</div><ul class="list_style_2"><li>
          <a href="view.do?program_seq=1">2026 미래교육 강좌</a>
          <div class="text"><div class="txt">
            <p>기간 : 2026년 8월 ~ 9월</p><p>대상 : 초등학생</p><p>장소 : 학교</p>
          </div></div>
        </li></ul>
        """,
        "lxml",
    )
    oneclick_row = yongin._parse_oneclick_cards(oneclick_soup)[0][0]
    _assert_locked_classification(yicf_row)
    _assert_locked_classification(oneclick_row)


def test_yicf_detail_maps_all_required_display_fields() -> None:
    row = {
        "title": "공생 강좌",
        "start_date": "2026-08-01",
        "end_date": "2026-08-22",
        "target": "성인",
        "raw_fields": {"source_status": "신청중"},
    }
    pairs = [
        ("강좌명", row["title"]),
        ("수강일", "2026.08.01 ~ 2026.08.22"),
        ("접수일", "2026.07.01 ~ 2026.07.31"),
        ("강습시간", "10시 30분 ~ 12시 00분"),
        ("수강요일", "토"),
        ("연령", "성인"),
        ("정원", "12명"),
        ("수강료", "12,000원"),
        ("장소", "워크룸 1"),
        ("과정", "문화예술교육 > 여름학기"),
    ]
    soup = BeautifulSoup(
        "<table>"
        + "".join(
            f"<tr><th>{escape(key)}</th><td>{escape(value)}</td></tr>"
            for key, value in pairs
        )
        + "</table>",
        "lxml",
    )

    merged = yongin._merge_yicf_detail(row, soup)

    assert merged["period"] == "2026-08-01 ~ 2026-08-22"
    assert merged["apply_period"] == "2026-07-01 ~ 2026-07-31"
    assert merged["schedule_raw"] == "토 10시 30분 ~ 12시 00분"
    assert merged["fee"] == "12,000원"
    assert merged["venue_name"] == "워크룸 1"
    assert merged["category_raw"] == "문화예술교육 > 여름학기"


def test_yiyf_missing_official_detail_is_retained_without_template_text() -> None:
    row = {
        "title": "목록 전용 강좌", "branch": "수지청소년문화의집",
        "raw_fields": {"source_status": "접수중"},
    }
    soup = BeautifulSoup("<html><h2>프로그램안내</h2><h3>수강료 미납안내</h3></html>", "lxml")
    merged = yongin._merge_yiyf_detail(row, soup)
    assert merged["detail_unavailable_by_source"] is True
    assert merged["description"] if "description" in merged else True
    assert "phone" not in merged and "email" not in merged


def test_yiyf_official_delay_page_is_retried_or_retained_with_required_fields() -> None:
    row = {
        "title": "2차 베이킹교실A 7세(유아)~초6",
        "branch": "수지청소년문화의집",
        "schedule": "(토) 13:50-15:20",
        "fee": "청소년 : 48,000원",
        "raw_fields": {
            "source_category": "베이킹교실",
            "source_period_raw": "-",
            "source_status": "접수중",
        },
    }
    soup = BeautifulSoup(
        """
        <div class="sub_contents">
          <div class="sec_img1"><img src="../images/sub/delay_ico.png"></div>
          <div class="delay">
            <p>현재 사용자가 많습니다.</p>
            <span>원활한 접속을 위해서 다시 시도해 주시기 바랍니다.</span>
          </div>
        </div>
        """,
        "lxml",
    )

    assert yongin._is_yiyf_official_delay_page(soup) is True
    merged = yongin._merge_yiyf_detail(row, soup)

    assert merged["detail_temporarily_unavailable"] is True
    assert merged["period"] == "일정 미정"
    assert merged["schedule_raw"] == "(토) 13:50-15:20"
    assert merged["target"] == "7세(유아)~초6"
    assert merged["venue_name"] == "수지청소년문화의집"
    assert merged["category_raw"] == "베이킹교실"
    assert merged["raw_fields"]["source_venue_fallback_to_branch"] is True
    assert (
        merged["raw_fields"]["public_detail_state"]
        == "official_server_delay_after_retries"
    )


def test_yiyf_missing_list_period_is_recovered_from_public_detail() -> None:
    row = {
        "title": "자유헬스", "branch": "용인청소년수련관(체육)",
        "schedule": "월~금", "raw_fields": {"source_period_raw": "-"},
    }
    pairs = [
        ("시설명", "용인청소년수련관(체육)"),
        ("강습기간", "2026-07-23 ~ 2026-08-22"),
        ("강습시간", "월~금"),
    ]
    soup = BeautifulSoup(
        '<div class="board-view">자유헬스' + "".join(
            f"<dl><dt>{key}</dt><dd>{value}</dd></dl>" for key, value in pairs
        ) + "</div>",
        "lxml",
    )
    merged = yongin._merge_yiyf_detail(row, soup)
    assert (merged["start_date"], merged["end_date"]) == (
        "2026-07-23", "2026-08-22"
    )
    assert merged["period"] == "2026-07-23 ~ 2026-08-22"
    assert merged["schedule_raw"] == "월~금"
    assert merged["venue_name"] == "용인청소년수련관(체육)"
    assert merged["source_period_recovered_from_detail"] is True


def test_oneclick_title_year_repairs_the_single_official_source_year_typo() -> None:
    start, end, _, corrected = yongin._oneclick_period(
        "2026 용인교육공동체", "2025년 8월 ~ 12월"
    )
    assert (start, end, corrected) == ("2026-08-01", "2026-12-31", True)


def test_oneclick_detail_fills_required_fields_with_source_provenance() -> None:
    row = {
        "title": "2026 미래교육 강좌",
        "start_date": "2026-08-01",
        "end_date": "2026-12-31",
        "target": "초등학생",
        "venue_name": "선정학교",
        "raw_fields": {"programme_period_raw": "2026년 8월 ~ 12월"},
    }
    pairs = [
        ("프로그램명", row["title"]),
        ("운영기간", "2026년 8월 ~ 12월(일정 협의)"),
        ("접수기간", "2026.07.21 ~ 2026.07.31"),
        ("대상", "초등학생"),
        ("장소", "선정학교"),
        ("운영방법", "2회기(총 4차시) 운영"),
    ]
    soup = BeautifulSoup(
        "<table>"
        + "".join(
            f"<tr><th>{escape(key)}</th><td>{escape(value)}</td></tr>"
            for key, value in pairs
        )
        + "</table>",
        "lxml",
    )

    merged = yongin._merge_oneclick_detail(
        row, soup, date(2026, 7, 28)
    )

    assert merged["period"] == "2026-08-01 ~ 2026-12-31"
    assert merged["apply_period"] == "2026-07-21 ~ 2026-07-31"
    assert merged["schedule_raw"] == "일정 협의"
    assert merged["fee"] == "요금 별도 안내"
    assert merged["category_raw"] == "교육·강좌"
    assert merged["raw_fields"]["source_fee_omitted"] is True
    assert merged["raw_fields"]["source_time_omitted"] is False


def test_pii_is_never_retained_in_public_description() -> None:
    with pytest.raises(yongin.YonginContractError):
        yongin._assert_rows_public([{"description": "문의 010-1234-5678"}])
    with pytest.raises(yongin.YonginContractError):
        yongin._assert_rows_public([{"email": "person@example.com"}])


def test_existing_parent_owners_and_subset_aliases_are_non_executing() -> None:
    assert yongin.YONGIN_EXISTING_LIFELONG_PROVIDER == "YONGIN_LIFELONG_LEARNING"
    assert yongin.YONGIN_GSEEK_PARENT_PROVIDER == "GYEONGGI_GSEEK"
    assert any(
        item["owner"] == "YONGIN_LIFELONG_LEARNING"
        and "sibling_tab" in item["reason"]
        for item in yongin.YONGIN_NON_EXECUTING_ALIASES
    )
    assert any(
        item["owner"] == "GYEONGGI_GSEEK"
        and "4146000000" in item["reason"]
        for item in yongin.YONGIN_NON_EXECUTING_ALIASES
    )


def test_cross_owner_overlap_reports_but_does_not_merge_parent_owners() -> None:
    result = yongin.yongin_cross_owner_overlap({
        "A": [{"title": "같은 강좌", "start_date": "2026-08-01", "end_date": "2026-08-02"}],
        "B": [{"title": "같은 강좌", "start_date": "2026-08-01", "end_date": "2026-08-02"}],
    })
    assert result["overlap_count"] == 1
    assert result["overlaps"][0]["providers"] == ["A", "B"]
    assert result["lifelong_owner_merged"] is False
    assert result["gseek_parent_merged"] is False
    undated = yongin.yongin_cross_owner_overlap({
        "A": [{"title": "요가"}],
        "B": [{"title": "요가"}],
    })
    assert undated["overlap_count"] == 0


def test_managed_session_is_required_by_default() -> None:
    rows, _, meta = yongin.collect_yongin_reservation_courses(
        _target(yongin.YONGIN_RESERVATION_PROVIDER, yongin.YONGIN_RESERVATION_URL)
    )
    assert rows == []
    assert "managed session_factory is required" in meta["configured_collection_error"]


def _identity(rows: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    return sorted(
        (
            str(row.get("provider_course_id", "")),
            str(row.get("title", "")),
            str(row.get("start_date", "")),
            str(row.get("end_date", "")),
        )
        for row in rows
    )


@pytest.mark.skipif(
    os.environ.get("YONGIN_LIVE_TESTS") != "1",
    reason="set YONGIN_LIVE_TESTS=1 for the official two-run live audit",
)
@pytest.mark.parametrize(("provider", "url"), OWNER_PAIRS)
def test_live_owner_is_complete_public_and_stable_twice(provider: str, url: str) -> None:
    limits = dict(yongin.YONGIN_RECOMMENDED_LIMITS[provider])
    collector = (
        yongin.collect_yongin_jachi_courses
        if provider in yongin.YONGIN_JACHI_OWNERS
        else yongin.collect_yongin_education_courses
    )
    results = []
    for _ in range(2):
        rows, _, meta = collector(
            _target(provider, url),
            today=date(2026, 7, 23),
            detail_workers=12,
            allow_raw_requests_for_tests=True,
            **limits,
        )
        assert rows, meta.get("configured_collection_error")
        assert meta["snapshot_complete"] is True
        assert meta["full_snapshot_validated"] is True
        assert meta["details_complete"] is True
        assert meta["application_endpoints_called"] == 0
        assert not any(yongin._contains_pii(row.get("description")) for row in rows)
        results.append(_identity(rows))
    assert results[0] == results[1]
