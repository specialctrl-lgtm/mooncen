from __future__ import annotations

from dataclasses import dataclass

import pytest

from Crawler import municipal_wonju as wonju


@dataclass
class FakeResponse:
    url: str
    body: str
    status_code: int = 200
    history: tuple[object, ...] = ()

    @property
    def content(self) -> bytes:
        return self.body.encode("utf-8")


class FakeSession:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.requests: list[str] = []
        self.closed = False

    def get(self, url: str, *, timeout: int, allow_redirects: bool) -> FakeResponse:
        assert timeout > 0
        assert allow_redirects is False
        self.requests.append(url)
        if url not in self.pages:
            raise AssertionError(f"unexpected URL: {url}")
        return FakeResponse(url, self.pages[url])

    def close(self) -> None:
        self.closed = True


def _shell(owner: str, content: str) -> str:
    if owner == "municipal":
        return f"""
        <html><head><title>교육/강좌 신청(전체) - 원주시 통합예약플랫폼</title></head>
        <body id="www" class="www page74">{content}
        <footer><address>강원특별자치도 원주시 시청로1</address></footer></body></html>
        """
    return f"""
    <html><head><title>원주교육문화관</title></head><body>{content}
    <footer><div class="footer_info"><address>
    강원특별자치도 원주시 북원로 2312 (단계동) 원주교육문화관
    </address></div></footer></body></html>
    """


def _institutions() -> str:
    return """
    <form class="search detail"><select name="schInstt">
      <option value="">전체</option>
      <option value="IN00000001">학습관</option>
      <option value="IN00000002">시민정보화교육장(일산동)</option>
    </select></form>
    """


def _municipal_card(
    identity: str,
    title: str,
    *,
    page: int,
    status: str = "접수중",
    room: str = "101호",
    list_capacity: str = "3/10 (0/0)",
) -> str:
    href = (
        "./eduLectureWebView.do?key=74&prgNo=" + identity
        + f"&pageUnit=8&pageIndex={page}&searchCnd=all"
    )
    return f"""
    <li class="thumbnail_item service1"><a class="thumbnail_anchor" href="{href}">
      <span class="thumbnail_content">
        <span class="stat">{status}</span><span class="place">중앙동</span>
        <span class="price free">무료</span><span class="thumbnail_sub">{title}</span>
        <span class="info">
          <span class="info_item"><span class="info_sub">장소</span>{room}</span>
          <span class="info_item"><span class="info_sub">대상</span>성인</span>
          <span class="info_item"><span class="info_sub">접수</span>2026-07-01 ~ 2026-07-31</span>
          <span class="info_item"><span class="info_sub">운영</span>2026-08-01 ~ 2026-08-31</span>
          <span class="info_item"><span class="info_sub">신청/정원(대기)</span>{list_capacity}</span>
        </span>
      </span>
    </a></li>
    """


def _municipal_page(*, sentinel_text: str = "검색하신 내용을 찾을 수 없습니다.") -> tuple[str, str]:
    last_href = "./eduLectureAllWebList.do?key=74&pageUnit=8&searchCnd=all&pageIndex=1"
    cards = _municipal_card("101", "온라인 강좌", page=1) + _municipal_card(
        "102", "전화 강좌", page=1, room="건강문화센터 5층", list_capacity="0/0 (0/10)"
    )
    first = _shell(
        "municipal",
        f"""
        {_institutions()}
        <div class="bbs_page"><span class="item count"><em>2</em></span>
        <span class="item page"><em>1/1</em></span></div>
        <ul class="thumbnail_list">{cards}</ul>
        <div class="p-page"><a class="p-page__link active">1</a>
        <a class="next-end" href="{last_href}">끝</a></div>
        """,
    )
    sentinel = _shell(
        "municipal",
        f"""
        {_institutions()}
        <div class="bbs_page"><span class="item count"><em>2</em></span>
        <span class="item page"><em>2/1</em></span></div>
        <div class="p-empty"><div class="inner"><p class="tit">{sentinel_text}</p></div></div>
        <div class="p-empty"><div class="inner"><p class="tit">{sentinel_text}</p></div></div>
        <div class="p-page"><a class="next-end" href="{last_href}">끝</a></div>
        """,
    )
    return first, sentinel


def _municipal_detail(
    identity: str,
    title: str,
    *,
    branch: str,
    method: str,
    capacity: str,
    applications: bool,
    status: str = "접수중",
    room: str = "101호",
    application_href: str | None = None,
) -> str:
    schema = min(wonju._MUNICIPAL_DETAIL_SCHEMAS, key=len)
    values = {
        "운영기관": branch,
        "년도/기수": "2026년 1기",
        "카테고리": "정보화",
        "과목": title,
        "대상": "성인",
        "장소": room,
        "주소": "강원특별자치도 원주시 테스트로 1",
        "접수기간": "2026-07-01 09:00 ~ 2026-07-31 18:00",
        "운영기간": "2026-08-01 ~ 2026-08-31",
        "운영시간": "10:00 ~ 12:00",
        "운영요일": "화",
        "모집/신청": capacity,
        "이용요금": "무료",
        "교재비": "0 원",
        "재료비": "0 원",
        "신청방법": method,
        "선발방식": "선착순",
        "문의전화": "033-737-0000",
        "강의계획서 정보제공": "",
    }
    table = "".join(f"<tr><th>{label}</th><td>{values[label]}</td></tr>" for label in schema)
    controls = ""
    if applications:
        href = application_href or f"./eduApplicantWebAgree.do?key=74&prgNo={identity}"
        control = f'<a href="{href}" onclick="fn_aplcnt(this.href); return false;">신청</a>'
        controls = control + control
    mypage = '<a href="./eduApplicantMypageList.do?key=131">확인</a>' * 2
    return _shell(
        "municipal",
        f"""
        <div class="program program_view edu">
          <div class="view_topbox"><span class="stat">{status}</span>
          <span class="topbox_sub">{title}</span></div>
          <table class="table type2"><tbody>{table}</tbody></table>
          {controls}{mypage}<a href="./downloadEduLectureFile.do?prgNo={identity}&fn=a.pdf">첨부</a>
        </div>
        """,
    )


def _municipal_fixture(
    *,
    sentinel_text: str = "검색하신 내용을 찾을 수 없습니다.",
    detail_title: str = "온라인 강좌",
    application_href: str | None = None,
) -> dict[str, str]:
    first, sentinel = _municipal_page(sentinel_text=sentinel_text)
    return {
        wonju.municipal_list_url(1): first,
        wonju.municipal_list_url(2): sentinel,
        "https://yeyak.wonju.go.kr/www/eduLectureWebView.do?key=74&prgNo=101&pageUnit=8&pageIndex=1&searchCnd=all": _municipal_detail(
            "101", detail_title, branch="학습관", method="온라인접수",
            capacity="모집인원 : 10 명, 신청인원 : 3 명", applications=True,
            application_href=application_href,
        ),
        "https://yeyak.wonju.go.kr/www/eduLectureWebView.do?key=74&prgNo=102&pageUnit=8&pageIndex=1&searchCnd=all": _municipal_detail(
            "102", "전화 강좌", branch="시민정보화교육장(일산동)", method="전화접수",
            capacity="모집인원 : 35 명, 온라인신청 : 0 명, 오프라인신청 : 27 명",
            applications=False, room="건강문화센터 5층",
        ),
    }


def _gwe_card(identity: str, title: str, *, mode: str = "open") -> str:
    if mode == "open":
        capacity = "선착순 : 0 / 12 (대기자 : 0 / 5)"
        primary = (
            f'<button class="applyStatusButton" data-event-id="{identity}" '
            f'data-event-title="{title}">접수중</button>'
        )
    elif mode == "waiting":
        capacity = "선착순 : 12 / 12 (대기자 : 1 / 5)"
        primary = (
            f'<button class="waiting reserveStatusApplyButton" data-event-id="{identity}" '
            f'data-event-title="{title}">대기자접수</button>'
        )
    elif mode == "full":
        capacity = "선착순 : 12 / 12 (대기자 : 5 / 5)"
        primary = ""
    else:
        raise AssertionError(f"unsupported GWE fixture mode: {mode}")
    return f"""
    <li class="lecture_item">
      <span class="lecture_item__library">원주교육문화관</span>
      <div class="lecture_item__title"><a href="/wjecc/menu/4555/lecture-event/{identity}">{title}</a></div>
      <dl class="lecture_item__info">
        <dt>신청기간</dt><dd>2026.07.01 ~ 2026.07.31</dd>
        <dt>운영기간</dt><dd>2026.08.01 ~ 2026.08.31</dd>
        <dt>신청대상</dt><dd>성인</dd><dt>모집방법</dt><dd>선착순</dd>
        <dt>모집인원</dt><dd>{capacity}</dd>
      </dl>
      <div class="lecture_item__button">
        {primary}
        <button class="registrationCheckButton" data-event-id="{identity}" data-event-title="{title}"
          data-category-name="평생교육">신청확인</button>
      </div>
    </li>
    """


def _gwe_pages(*, empty_text: str = "조회되는 문화강좌가 없습니다.") -> tuple[str, str]:
    first = _shell(
        "gwe",
        f"""
        <div class="lecture_result_top__count"><strong>2</strong></div>
        <ul class="lecture_result_list">
          {_gwe_card("9001", "실제 강좌")}
          {_gwe_card(wonju.WONJU_GWE_TRAINING_ID, wonju.WONJU_GWE_TRAINING_TITLE)}
        </ul>
        <div class="paging_container"><strong class="current" data-page-no="0">1</strong></div>
        """,
    )
    sentinel = _shell(
        "gwe",
        f"""
        <div class="lecture_result_top__count"><strong>2</strong></div>
        <ul class="lecture_result_list"><li class="no_data">{empty_text}</li></ul>
        """,
    )
    return first, sentinel


def _gwe_detail(
    identity: str,
    title: str,
    *,
    non_user: str = "Y",
    mode: str = "open",
) -> str:
    if mode == "open":
        capacity = "선착순 : 0 / 12 (대기자 : 0 / 5)"
        status = '<span><em class="lecture_detail__status">접수중</em></span>'
        application = (
            f'<button id="applyButton" class="btn_ico_apply" '
            f'data-event-id="{identity}" data-event-title="{title}" '
            f'data-non-user-apply-yn="{non_user}">신청</button>'
        )
    elif mode == "waiting":
        capacity = "선착순 : 12 / 12 (대기자 : 1 / 5)"
        status = '<span><strong class="lecture_detail__status waiting">대기자접수</strong></span>'
        application = (
            f'<button id="reserveApplyButton" class="btn_ico_apply" '
            f'data-event-id="{identity}" data-event-title="{title}" '
            f'data-non-user-apply-yn="{non_user}">대기자신청</button>'
        )
    elif mode == "full":
        capacity = "선착순 : 12 / 12 (대기자 : 5 / 5)"
        status = ""
        application = ""
    else:
        raise AssertionError(f"unsupported GWE fixture mode: {mode}")
    values = {
        "강사명": "폐기 대상 강사",
        "도서관": "원주교육문화관",
        "운영기간": "2026.08.01 ~ 2026.08.31",
        "운영시간": "화 10:00 ~ 12:00",
        "신청방법": "온라인",
        "신청기간": "2026.07.01 ~ 2026.07.31",
        "신청자격": "누구나",
        "신청대상": "성인",
        "모집인원": capacity,
        "준비물": "없음",
        "재료비": "-",
        "참가비": "-",
        "장소": "강의실",
    }
    pairs = "".join(f"<dt>{label}</dt><dd>{values[label]}</dd>" for label in wonju._GWE_DETAIL_SCHEMA)
    return _shell(
        "gwe",
        f"""
        <article class="lecture_detail">
          <h2 class="lecture_detail__title">{title}{status}</h2>
          <div class="lecture_detail__info"><dl class="lecture_detail__dl">{pairs}</dl></div>
          <div class="lecture_detail__content">저장하면 안 되는 자유서술</div>
          <button class="btnDownload" data-id="file-{identity}">첨부</button>
        </article>
        <div class="btn_container">{application}</div>
        """,
    )


def _gwe_fixture(*, empty_text: str = "조회되는 문화강좌가 없습니다.") -> dict[str, str]:
    first, sentinel = _gwe_pages(empty_text=empty_text)
    return {
        wonju.gwe_list_url(0): first,
        wonju.gwe_list_url(1): sentinel,
        "https://lib.gwe.go.kr/wjecc/menu/4555/lecture-event/9001": _gwe_detail(
            "9001", "실제 강좌", non_user="Y"
        ),
        f"https://lib.gwe.go.kr/wjecc/menu/4555/lecture-event/{wonju.WONJU_GWE_TRAINING_ID}": _gwe_detail(
            wonju.WONJU_GWE_TRAINING_ID, wonju.WONJU_GWE_TRAINING_TITLE, non_user="N"
        ),
    }


def _run(provider: str, url: str, pages: dict[str, str], **kwargs: object):
    session = FakeSession(pages)
    rows, parser, meta = wonju.collect_wonju_education(
        {"provider": provider, "url": url},
        today="2026-07-23",
        session_factory=lambda: session,
        fetcher=lambda current, request_url, timeout: current.get(
            request_url, timeout=timeout, allow_redirects=False
        ),
        **kwargs,
    )
    return session, rows, parser, meta


def test_exact_targets_retain_two_owners_and_reject_home_alias() -> None:
    assert wonju.owner_for_target(
        {"provider": wonju.WONJU_MUNICIPAL_PROVIDER, "url": wonju.WONJU_MUNICIPAL_URL}
    ) == "municipal"
    assert wonju.owner_for_target(
        {"provider": wonju.WONJU_GWE_PROVIDER, "url": wonju.WONJU_GWE_URL}
    ) == "gwe"
    assert not wonju.is_target(
        {"provider": wonju.WONJU_DISABLED_HOME_ALIAS_PROVIDER, "url": wonju.WONJU_HOME_ALIAS_URL}
    )
    assert not wonju.is_target(
        {"provider": wonju.WONJU_MUNICIPAL_PROVIDER, "url": wonju.WONJU_MUNICIPAL_URL + "&extra=1"}
    )


def test_managed_session_is_required_by_default() -> None:
    rows, _, meta = wonju.collect_wonju_education(
        {"provider": wonju.WONJU_GWE_PROVIDER, "url": wonju.WONJU_GWE_URL}
    )
    assert rows == []
    assert meta["configured_collection_error"] == "managed session_factory injection is required"


@pytest.mark.parametrize(
    "url,owner",
    [
        ("https://evil.example/wjecc/menu/4555/lecture-event/list/all", "gwe"),
        (wonju.WONJU_GWE_URL + "?page=1&page=2", "gwe"),
        (wonju.WONJU_GWE_URL + "#fragment", "gwe"),
        ("https://lib.gwe.go.kr/api/homepage/wjecc/lecture-event/9001/applies", "gwe"),
        ("https://yeyak.wonju.go.kr/www/eduApplicantWebAgree.do?key=74&prgNo=101", "municipal"),
        ("https://yeyak.wonju.go.kr/www/downloadEduLectureFile.do?prgNo=101", "municipal"),
    ],
)
def test_fetch_allowlist_rejects_unsafe_or_noncanonical_urls(url: str, owner: str) -> None:
    with pytest.raises(wonju.WonjuContractError):
        wonju._validate_fetch_url(url, owner)


def test_municipal_complete_snapshot_includes_online_and_offline_courses() -> None:
    session, rows, parser, meta = _run(
        wonju.WONJU_MUNICIPAL_PROVIDER, wonju.WONJU_MUNICIPAL_URL, _municipal_fixture()
    )
    assert parser == wonju.WONJU_MUNICIPAL_PARSER
    assert [row["title"] for row in rows] == ["온라인 강좌", "전화 강좌"]
    assert [row["branch"] for row in rows] == ["학습관", "시민정보화교육장(일산동)"]
    assert [row["application_type"] for row in rows] == ["ONLINE_RESERVATION", "OFFLINE_APPLY"]
    assert rows[1]["capacity_total"] == 35
    assert rows[1]["capacity_current"] == 27
    assert meta["source_total_count"] == meta["date_current_source_count"] == meta["row_count"] == 2
    assert meta["declared_pages"] == 1
    assert meta["post_last_empty_verified"] is True
    assert meta["details_complete"] is True
    assert meta["stable_boundary_recheck"] is True
    assert meta["application_endpoint_requests"] == 0
    assert meta["mypage_endpoint_requests"] == 0
    assert meta["attachment_endpoint_requests"] == 0
    assert all("Applicant" not in url and "download" not in url for url in session.requests)
    assert all("033-737-0000" not in repr(row) for row in rows)


def test_gwe_zero_based_snapshot_excludes_verified_training_shell() -> None:
    session, rows, parser, meta = _run(
        wonju.WONJU_GWE_PROVIDER, wonju.WONJU_GWE_URL, _gwe_fixture()
    )
    assert parser == wonju.WONJU_GWE_PARSER
    assert [row["title"] for row in rows] == ["실제 강좌"]
    assert rows[0]["application_type"] == "ONLINE_RESERVATION"
    assert meta["source_total_count"] == 2
    assert meta["date_current_source_count"] == 2
    assert meta["excluded_training_count"] == 1
    assert meta["row_count"] == 1
    assert meta["final_page_size"] == 2
    assert meta["post_last_empty_verified"] is True
    assert meta["attachment_controls_discarded"] == 2
    assert meta["application_endpoint_requests"] == 0
    assert all("/api/" not in url and "login" not in url and "Download" not in url for url in session.requests)
    assert "폐기 대상 강사" not in repr(rows)
    assert "저장하면 안 되는 자유서술" not in repr(rows)


def test_gwe_full_capacity_and_waiting_control_variants() -> None:
    first = _shell(
        "gwe",
        f"""
        <div class="lecture_result_top__count"><strong>3</strong></div>
        <ul class="lecture_result_list">
          {_gwe_card("9257", "정원 마감 강좌", mode="full")}
          {_gwe_card("9255", "대기 접수 강좌", mode="waiting")}
          {_gwe_card(wonju.WONJU_GWE_TRAINING_ID, wonju.WONJU_GWE_TRAINING_TITLE)}
        </ul>
        <div class="paging_container"><strong class="current" data-page-no="0">1</strong></div>
        """,
    )
    sentinel = _shell(
        "gwe",
        """
        <div class="lecture_result_top__count"><strong>3</strong></div>
        <ul class="lecture_result_list"><li class="no_data">조회되는 문화강좌가 없습니다.</li></ul>
        """,
    )
    pages = {
        wonju.gwe_list_url(0): first,
        wonju.gwe_list_url(1): sentinel,
        "https://lib.gwe.go.kr/wjecc/menu/4555/lecture-event/9257": _gwe_detail(
            "9257", "정원 마감 강좌", mode="full"
        ),
        "https://lib.gwe.go.kr/wjecc/menu/4555/lecture-event/9255": _gwe_detail(
            "9255", "대기 접수 강좌", non_user="N", mode="waiting"
        ),
        f"https://lib.gwe.go.kr/wjecc/menu/4555/lecture-event/{wonju.WONJU_GWE_TRAINING_ID}": _gwe_detail(
            wonju.WONJU_GWE_TRAINING_ID, wonju.WONJU_GWE_TRAINING_TITLE, non_user="N"
        ),
    }
    _, rows, _, meta = _run(wonju.WONJU_GWE_PROVIDER, wonju.WONJU_GWE_URL, pages)
    assert [row["status"] for row in rows] == ["CLOSED", "WAITING"]
    assert [row["reservation_available"] for row in rows] == [False, True]
    assert [row["application_type"] for row in rows] == ["INFO_ONLY", "ONLINE_LOGIN_REQUIRED"]
    assert meta["application_controls_observed"] == 2


def test_owner_outputs_have_disjoint_stable_identities() -> None:
    _, municipal, _, municipal_meta = _run(
        wonju.WONJU_MUNICIPAL_PROVIDER, wonju.WONJU_MUNICIPAL_URL, _municipal_fixture()
    )
    _, gwe, _, gwe_meta = _run(wonju.WONJU_GWE_PROVIDER, wonju.WONJU_GWE_URL, _gwe_fixture())
    assert municipal_meta["snapshot_complete"] is True
    assert gwe_meta["snapshot_complete"] is True
    assert {row["provider_course_id"] for row in municipal}.isdisjoint(
        {row["provider_course_id"] for row in gwe}
    )
    assert {row["title"] for row in municipal}.isdisjoint({row["title"] for row in gwe})


@pytest.mark.parametrize(
    "provider,url,pages,error_fragment",
    [
        (
            wonju.WONJU_MUNICIPAL_PROVIDER,
            wonju.WONJU_MUNICIPAL_URL,
            _municipal_fixture(sentinel_text="결과 없음"),
            "exact empty sentinel drift",
        ),
        (
            wonju.WONJU_GWE_PROVIDER,
            wonju.WONJU_GWE_URL,
            _gwe_fixture(empty_text="강좌 없음"),
            "exact empty sentinel drift",
        ),
        (
            wonju.WONJU_MUNICIPAL_PROVIDER,
            wonju.WONJU_MUNICIPAL_URL,
            _municipal_fixture(detail_title="목록과 다른 제목"),
            "list/detail disagreement",
        ),
        (
            wonju.WONJU_MUNICIPAL_PROVIDER,
            wonju.WONJU_MUNICIPAL_URL,
            _municipal_fixture(
                application_href="https://evil.example/www/eduApplicantWebAgree.do?key=74&prgNo=101"
            ),
            "application endpoint binding drift",
        ),
    ],
)
def test_contract_drift_fails_closed(
    provider: str, url: str, pages: dict[str, str], error_fragment: str
) -> None:
    _, rows, _, meta = _run(provider, url, pages)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error_fragment in meta["configured_collection_error"]


def test_source_caps_and_dedupe_cardinality_fail_closed() -> None:
    _, rows, _, meta = _run(
        wonju.WONJU_MUNICIPAL_PROVIDER,
        wonju.WONJU_MUNICIPAL_URL,
        _municipal_fixture(),
        detail_limit=1,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit" in meta["configured_collection_error"]

    _, rows, _, meta = _run(
        wonju.WONJU_MUNICIPAL_PROVIDER,
        wonju.WONJU_MUNICIPAL_URL,
        _municipal_fixture(),
        dedupe_rows=lambda values: values[:1],
    )
    assert rows == []
    assert "dedupe_rows changed complete identity cardinality" in meta["configured_collection_error"]


def test_aggregate_runner_bounds_use_stricter_wonju_effective_caps() -> None:
    _, rows, _, meta = _run(
        wonju.WONJU_MUNICIPAL_PROVIDER,
        wonju.WONJU_MUNICIPAL_URL,
        _municipal_fixture(),
        max_pages=1_500,
        detail_limit=3_000,
    )
    assert len(rows) == 2
    assert meta["snapshot_complete"] is True


def test_privacy_allowlist_rejects_pii_or_free_text_escape() -> None:
    row = {
        "title": "강좌",
        "description": "문의 033-737-0000",
        "raw_fields": {"identity": "1", "service_family": "education"},
    }
    assert "PII-like value escaped allowlist" in wonju._privacy_errors(row)
    row = {"title": "강좌", "content_html": "본문", "raw_fields": {"identity": "1"}}
    assert "forbidden PII/free-text key" in wonju._privacy_errors(row)
