from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import ssl
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from bs4 import Tag
import pytest

from Crawler import municipal_hongcheon as hongcheon
from tools.promote_municipal_integrated_reservation_targets import (
    candidate_id,
    normalized_duplicate_url,
    stable_provider,
)
from utils.outbound_http import SafeSession


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
        status: int = 200,
        final_url: str | None = None,
        history: list[Any] | None = None,
    ) -> None:
        self.url = final_url if final_url is not None else url
        self.content = text.encode("utf-8")
        self.status_code = status
        self.history = history or []
        self.headers = {"Content-Type": "text/html;charset=utf-8"}


class FakeSession:
    def __init__(self, route: Callable[[str, int], Response]) -> None:
        self.route = route
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.counts: dict[str, int] = {}

    def get(self, url: str, **kwargs: Any) -> Response:
        self.calls.append(("GET", url, kwargs))
        self.counts[url] = self.counts.get(url, 0) + 1
        return self.route(url, self.counts[url])

    def post(self, url: str, **kwargs: Any) -> Response:
        self.calls.append(("POST", url, kwargs))
        raise AssertionError(f"unexpected POST {url}")

    def close(self) -> None:
        return None


def _target() -> Target:
    return Target(
        provider=hongcheon.HONGCHEON_LIBRARY_PROVIDER,
        url=hongcheon.HONGCHEON_LIBRARY_URL,
    )


def _f508_target() -> Target:
    return Target(
        provider=hongcheon.HONGCHEON_EXISTING_COURSE_PROVIDER,
        url=hongcheon.HONGCHEON_EXISTING_COURSE_URL,
    )


def _branch_label(branch: hongcheon.HongcheonLibraryBranch) -> str:
    return branch.name.replace("홍천어린이도서관", "어린이도서관").replace(
        "도서관", " 도서관"
    )


def _directory_html(*, omit_last: bool = False, extra: bool = False) -> str:
    branches = hongcheon.HONGCHEON_LIBRARY_BRANCHES[:-1] if omit_last else hongcheon.HONGCHEON_LIBRARY_BRANCHES
    links = "".join(
        f'<a href="/{branch.site}/index.do">{_branch_label(branch)}</a>'
        for branch in branches
    )
    if extra:
        links += '<a href="/other/index.do">그 외 도서관</a>'
    return f"""
    <html><head><title>홍천군립도서관</title></head><body>
      <div class="libarary_btn_wrap">{links}</div>
    </body></html>
    """


def _identity(branch: hongcheon.HongcheonLibraryBranch) -> tuple[str, str, str]:
    number = hongcheon.HONGCHEON_LIBRARY_BRANCHES.index(branch) + 1
    return str(10 + number), "0", str(100 + number)


def _list_row(
    branch: hongcheon.HongcheonLibraryBranch,
    *,
    title_suffix: str = "",
    status: str = "수강신청",
) -> str:
    group_idx, category_idx, teach_idx = _identity(branch)
    title = f"{branch.name} 여름 독서교실{title_suffix}"
    active = status in {"수강신청", "대기자신청"}
    control = ""
    if active:
        apply_status = "1" if status == "수강신청" else "2"
        control = (
            '<a href="" class="add reg" '
            f'keyvalue1="{branch.homepage_id}" keyvalue2="{group_idx}" '
            f'keyvalue3="{category_idx}" keyvalue4="{teach_idx}" '
            f'keyvalue5="16" apply_status="{apply_status}">{status}</a>'
        )
    else:
        control = f'<span class="class_end">{status}</span>'
    row_class = "" if active else ' class="reg_end"'
    return f"""
      <tr{row_class}>
        <td class="list_cate_group sort">여름특강</td>
        <td class="title"><a href="#" title="강좌 상세정보 보기"
          class="detail-btn teach_title" keyvalue1="{group_idx}"
          keyvalue2="{category_idx}" keyvalue3="{teach_idx}">{title}</a></td>
        <td class="r_date">2026-07-01 09:00 ~ 2026-07-31 18:00</td>
        <td class="t_date">2026-08-01 ~ 2026-08-08 ( 토 ) 10:00 ~ 12:00</td>
        <td class="person">온라인접수 1 / 10 (후보자 0 / 2)</td>
        <td class="target">홍천군민</td>
        <td class="state">{control}</td>
      </tr>
    """


def _catalogue_html(
    branch: hongcheon.HongcheonLibraryBranch,
    *,
    sentinel: bool = False,
    title_suffix: str = "",
    status: str = "수강신청",
) -> str:
    body = "" if sentinel else _list_row(branch, title_suffix=title_suffix, status=status)
    return f"""
    <html><head><title>{branch.page_title} &gt; 문화공간 &gt; 프로그램</title></head>
    <body><table class="table class_table culture_table mobile_table">
      <caption>문화행사신청 목록 페이지</caption>
      <thead><tr>
        <th>분류</th><th>제목</th><th>정원 및 신청현황</th>
        <th>행사기간</th><th>접수기간</th><th>접수상태</th>
      </tr></thead><tbody>{body}</tbody>
    </table></body></html>
    """


def _detail_html(
    branch: hongcheon.HongcheonLibraryBranch,
    *,
    title_suffix: str = "",
    status: str = "수강신청",
) -> str:
    group_idx, category_idx, teach_idx = _identity(branch)
    title = f"{branch.name} 여름 독서교실{title_suffix}"
    active = status in {"수강신청", "대기자신청"}
    control = ""
    if active:
        detail_apply_status = "0" if status == "수강신청" else "1"
        control = (
            '<a href="" class="btn pri reg_btn add" '
            f'keyvalue1="{branch.homepage_id}" keyvalue2="{group_idx}" '
            f'keyvalue3="{category_idx}" keyvalue4="{teach_idx}" '
            f'apply_status="{detail_apply_status}">{status}</a>'
        )
    return f"""
    <html><head><title>{branch.page_title} &gt; 문화공간 &gt; 프로그램</title></head>
    <body>
      <div class="culture_intro">
        <strong class="teach_sort">여름특강</strong><h4>{title}</h4>
        <ul>
          <li><span class="sub_title">접수기간</span>
              <span class="con">2026-07-01 09:00 ~ 2026-07-31 18:00</span></li>
          <li><span class="sub_title">강의대상</span><span class="con">홍천군민</span></li>
          <li><span class="sub_title">강사명</span>
              <span class="poison">개인강사 010-1111-2222</span></li>
          <li><span class="sub_title">강의기간</span>
              <span class="con">2026-08-01 ~ 2026-08-08</span></li>
          <li><span class="sub_title">강의시간</span>
              <span class="con">토 ( 10:00 ~ 12:00 )</span></li>
          <li><span class="sub_title">강의장소</span>
              <span class="con">{branch.name} 문화강좌실</span></li>
          <li><span class="sub_title">강의계획서</span>
              <a class="poison" href="download/{teach_idx}.do">개인자료</a></li>
          <li><span class="sub_title">준비물/재료비</span>
              <span class="poison">민감한 자유서술</span></li>
        </ul>
        <table class="table culture_view_table">
          <thead><tr><th>현재 참여/모집</th><th>현재 오프라인/오프라인</th>
            <th>현재 대기자/대기자</th></tr></thead>
          <tbody><tr><td>1 명 / 10 명</td><td>0 명 / 0 명</td><td>0명 / 2 명</td></tr></tbody>
        </table>
      </div>
      {control}
      <div class="culture_info poison">담당자 033-430-9999 / private@example.test</div>
    </body></html>
    """


def _fixture(
    *,
    sentinel_leak_site: str = "",
    unstable_site: str = "",
    detail_mismatch_site: str = "",
    directory_drift: bool = False,
    detail_status: int = 200,
) -> FakeSession:
    def route(url: str, count: int) -> Response:
        if url == hongcheon.HONGCHEON_LIBRARY_URL:
            return Response(url, text=_directory_html(omit_last=directory_drift))
        for branch in hongcheon.HONGCHEON_LIBRARY_BRANCHES:
            list_url = hongcheon.hongcheon_library_list_url(branch.site)
            sentinel_url = hongcheon.hongcheon_library_list_url(branch.site, sentinel=True)
            group_idx, category_idx, teach_idx = _identity(branch)
            detail_url = hongcheon.hongcheon_library_detail_url(
                branch.site, group_idx, category_idx, teach_idx
            )
            if url == list_url:
                suffix = " 변경" if branch.site == unstable_site and count > 1 else ""
                return Response(url, text=_catalogue_html(branch, title_suffix=suffix))
            if url == sentinel_url:
                return Response(
                    url,
                    text=_catalogue_html(
                        branch,
                        sentinel=branch.site != sentinel_leak_site,
                    ),
                )
            if url == detail_url:
                suffix = " 불일치" if branch.site == detail_mismatch_site else ""
                return Response(
                    url,
                    text=_detail_html(branch, title_suffix=suffix),
                    status=detail_status,
                )
        raise AssertionError(f"unexpected URL {url}")

    return FakeSession(route)


def _f508_status(ordinal: int) -> str:
    if ordinal <= 3:
        return "온라인 접수중"
    if ordinal == 4:
        return "온라인 방문접수 접수마감"
    if ordinal == 5:
        return "방문접수 접수마감"
    return "온라인 접수마감"


def _f508_course_id(ordinal: int) -> str:
    return str(10_000 + ordinal)


def _f508_title(ordinal: int, suffix: str = "") -> str:
    return f"홍천 공개교육 {ordinal}{suffix}"


def _f508_target_values(ordinal: int) -> tuple[str, str]:
    if ordinal == 3:
        return "초등학생 3학년~6...", "초등학생 3학년~6학년"
    if ordinal == 26:
        return "이병규", "이병규"
    return "홍천군민", "홍천군민"


def _f508_counts(ordinal: int) -> tuple[int, int]:
    return (15, 20) if ordinal == 3 else (ordinal % 11, 20)


def _f508_application_control(
    ordinal: int,
    *,
    page_index: int,
    wrong_course: bool = False,
) -> str:
    course_id = _f508_course_id(ordinal + 1 if wrong_course else ordinal)
    href = (
        "./courseWebAppRegist.do;jsessionid=ABCDEF0123456789ABCDEF0123456789?"
        f"key=1196&course={course_id}&srcEdu=&srcYear=2027&srcQuarter=&"
        f"srcCategory=&srcTitle=&srcStatus=&pageIndex={page_index}"
    )
    return f'<a href="{href}" aria-label="신청하기"></a>'


def _f508_list_row(ordinal: int, *, title_suffix: str = "") -> str:
    course_id = _f508_course_id(ordinal)
    page_index = (ordinal - 1) // hongcheon.HONGCHEON_EXISTING_COURSE_PAGE_SIZE + 1
    list_target, _detail_target = _f508_target_values(ordinal)
    current, total = _f508_counts(ordinal)
    status = _f508_status(ordinal)
    control = (
        _f508_application_control(ordinal, page_index=page_index)
        if status == "온라인 접수중"
        else ""
    )
    if ordinal <= 49:
        period = "2026.08.01 ~ 2026.08.08"
    elif ordinal == 100:
        period = "2020.01.01 ~ 2020.01.07 / 2020.02.01 ~ 2020.02.07"
    else:
        period = "2020.01.01 ~ 2020.01.07"
    detail_url = hongcheon.hongcheon_existing_course_detail_url(course_id)
    return f"""
      <tr>
        <td>{ordinal}</td><td>인문/교양</td>
        <td><a href="{detail_url}">{_f508_title(ordinal, title_suffix)}</a></td>
        <td>{list_target}</td><td>{period}</td><td>{current}/{total}</td>
        <td>{status}{control}</td>
      </tr>
    """


def _f508_list_html(
    page_index: int,
    *,
    total: int = 1067,
    sentinel_leak: bool = False,
    title_suffix: str = "",
) -> str:
    page_size = hongcheon.HONGCHEON_EXISTING_COURSE_PAGE_SIZE
    data_pages = (total + page_size - 1) // page_size
    sentinel_page = data_pages + 1
    if page_index == sentinel_page:
        body = (
            _f508_list_row(total, title_suffix=" sentinel-leak")
            if sentinel_leak
            else '<tr><td colspan="7">등록된 게시물이 없습니다.</td></tr>'
        )
        active = ""
    else:
        first = (page_index - 1) * page_size + 1
        last = min(total, page_index * page_size)
        body = "".join(
            _f508_list_row(
                ordinal,
                title_suffix=title_suffix if ordinal == first else "",
            )
            for ordinal in range(first, last + 1)
        )
        active = f'<strong class="p-page__link active">{page_index}</strong>'
    pagination_links = "".join(
        f'<a href="{hongcheon.hongcheon_existing_course_list_url(page)}">{page}</a>'
        for page in range(1, data_pages + 1)
    )
    return f"""
    <html><head><title>일반교육 목록 - 교육신청 - 교육정보 - 평생학습관</title></head>
    <body>
      <div class="bbs_count">총 <strong>{total}</strong>건</div>
      <table class="bbs_default list">
        <caption>{hongcheon._F508_LIST_CAPTION}</caption>
        <thead><tr>{''.join(f'<th>{header}</th>' for header in hongcheon._F508_LIST_HEADERS)}</tr></thead>
        <tbody>{body}</tbody>
      </table>
      <div class="p-pagination">{active}{pagination_links}</div>
    </body></html>
    """


def _f508_detail_html(
    ordinal: int,
    *,
    title_suffix: str = "",
    wrong_application_course: bool = False,
) -> str:
    _list_target, detail_target = _f508_target_values(ordinal)
    current, total = _f508_counts(ordinal)
    capacity = f"{current}명 접수 / 총 {total}명 모집 (방문접수 0 명)"
    if ordinal == 3:
        capacity = (
            f"{current}명 접수 / 총 {total}명 모집 "
            f"(온라인: {total}명, 대기인원: 4명, 방문접수 0 명)"
        )
    page_index = (ordinal - 1) // hongcheon.HONGCHEON_EXISTING_COURSE_PAGE_SIZE + 1
    control = ""
    if _f508_status(ordinal) == "온라인 접수중":
        control = _f508_application_control(
            ordinal,
            page_index=page_index,
            wrong_course=wrong_application_course,
        )
        if ordinal == 3 and not wrong_application_course:
            control += _f508_application_control(ordinal, page_index=page_index)
    education_time = "" if ordinal in {48, 49} else "토 10:00~12:00"
    fields = (
        ("강좌명", _f508_title(ordinal, title_suffix), False),
        ("분야", "인문/교양", False),
        ("교육대상", detail_target, False),
        ("교육장소", "홍천군 평생학습관 강의실", False),
        ("모집인원", capacity, False),
        ("접수기간", "2026년 07월 01일 09시 ~ 2026년 07월 31일 18시", False),
        ("교육기간", "2026년 08월 01일 ~ 2026년 08월 08일", False),
        ("교육시간", education_time, False),
        ("강사명", "개인강사", True),
        ("수강료", "무료", False),
        ("재료비", "민감한 자유서술", True),
        ("교육내용", "담당자 010-1111-2222", True),
        ("문의전화", "033-430-9999", True),
        ("첨부파일", "private@example.test", True),
    )
    detail_rows = "".join(
        f'<tr><th>{label}</th><td class="{"poison" if poison else "safe"}">{value}</td></tr>'
        for label, value, poison in fields
    )
    return f"""
    <html><head><title>일반교육 상세 - 교육신청 - 교육정보 - 평생학습관</title></head>
    <body>
      <table class="bbs_default view"><caption>교육정보</caption>
        <tbody>{detail_rows}</tbody>
      </table>
      {control}
      <div class="poison">신청자 명단과 담당자 정보</div>
    </body></html>
    """


def _f508_fixture(
    *,
    sentinel_leak: bool = False,
    unstable_page: int = 0,
    detail_mismatch_ordinal: int = 0,
    application_mismatch_ordinal: int = 0,
) -> FakeSession:
    def route(url: str, count: int) -> Response:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == "/edu/selectCourseWebList.do":
            page_index = int(query["pageIndex"][0])
            suffix = " 변경" if page_index == unstable_page and count > 1 else ""
            return Response(
                url,
                text=_f508_list_html(
                    page_index,
                    sentinel_leak=sentinel_leak and page_index == 12,
                    title_suffix=suffix,
                ),
            )
        if parsed.path == "/edu/courseWebView.do":
            course_id = int(query["course"][0])
            ordinal = course_id - 10_000
            return Response(
                url,
                text=_f508_detail_html(
                    ordinal,
                    title_suffix=(
                        " 불일치" if ordinal == detail_mismatch_ordinal else ""
                    ),
                    wrong_application_course=(
                        ordinal == application_mismatch_ordinal
                    ),
                ),
            )
        raise AssertionError(f"unexpected URL {url}")

    return FakeSession(route)


def test_provider_candidate_city_and_canonical_hashes_are_stable() -> None:
    assert hongcheon.HONGCHEON_MUNICIPALITY_CODE == "5172000000"
    assert hongcheon.HONGCHEON_LIBRARY_PROVIDER == "MUNI_HONGCHEONLIB_GO_KR_17726A2C"
    assert hongcheon.HONGCHEON_LIBRARY_CANDIDATE_ID == "MUNI_IR_6DAF3DB95540"
    assert hongcheon.HONGCHEON_LIBRARY_PROVIDER == stable_provider(
        hongcheon.HONGCHEON_LIBRARY_URL
    )
    assert hongcheon.HONGCHEON_LIBRARY_CANDIDATE_ID == candidate_id(
        normalized_duplicate_url(hongcheon.HONGCHEON_LIBRARY_URL)
    )
    assert hongcheon.HONGCHEON_EXISTING_COURSE_PROVIDER == (
        "MUNI_WWW_HONGCHEON_GO_KR_F5083BE8"
    )
    assert hongcheon.HONGCHEON_EXISTING_COURSE_CANDIDATE_ID == (
        "MUNI_IR_EBF329238984"
    )
    assert hongcheon.HONGCHEON_EXISTING_COURSE_CANDIDATE_ID == candidate_id(
        normalized_duplicate_url(hongcheon.HONGCHEON_EXISTING_COURSE_URL)
    )
    assert hongcheon.HONGCHEON_DEFAULT_MAX_PAGES == 20
    assert hongcheon.HONGCHEON_DEFAULT_DETAIL_LIMIT == 200
    assert hongcheon.HONGCHEON_EXISTING_COURSE_RECOMMENDED_MAX_PAGES == 30
    assert hongcheon.HONGCHEON_EXISTING_COURSE_RECOMMENDED_DETAIL_LIMIT == 200
    assert hongcheon.HONGCHEON_DEFAULT_MAX_REQUESTS >= (
        2
        + hongcheon.HONGCHEON_DEFAULT_MAX_PAGES
        + hongcheon.HONGCHEON_DEFAULT_DETAIL_LIMIT
    )


def test_official_six_branch_directory_names_codes_and_addresses_are_exact() -> None:
    assert [branch.site for branch in hongcheon.HONGCHEON_LIBRARY_BRANCHES] == [
        "yblib",
        "sslib",
        "nammyeon",
        "naemyeon",
        "naru",
        "children",
    ]
    assert [branch.name for branch in hongcheon.HONGCHEON_LIBRARY_BRANCHES] == [
        "연봉도서관",
        "서석도서관",
        "남면도서관",
        "내면도서관",
        "별빛나루도서관",
        "홍천어린이도서관",
    ]
    assert len({branch.homepage_id for branch in hongcheon.HONGCHEON_LIBRARY_BRANCHES}) == 6
    assert len({branch.code for branch in hongcheon.HONGCHEON_LIBRARY_BRANCHES}) == 6
    assert all(branch.address.startswith("강원특별자치도 홍천군") for branch in hongcheon.HONGCHEON_LIBRARY_BRANCHES)


def test_owner_boundaries_exclude_existing_and_non_ledger_official_pages() -> None:
    audit = hongcheon.HONGCHEON_OWNER_BOUNDARY_AUDIT
    assert audit[hongcheon.HONGCHEON_LIBRARY_PROVIDER]["decision"] == (
        "canonical_current_county_library_six_partition_owner"
    )
    assert "existing_owner" in audit[hongcheon.HONGCHEON_EXISTING_COURSE_PROVIDER]["decision"]
    assert audit["OFFICIAL_HONGCHEON_YOUTH_RECRUITMENT_BOARD"]["decision"].startswith(
        "exclude_mixed_notice_board"
    )
    assert audit["OFFICIAL_HONGCHEON_MUGUNGHWA_ARBORETUM"]["decision"].startswith(
        "exclude_static_recurring"
    )
    assert hongcheon.HONGCHEON_DISCOVERY_AUDIT["source_rows"] == 303
    assert hongcheon.HONGCHEON_DISCOVERY_AUDIT["current_or_future_rows"] == 15
    assert hongcheon.HONGCHEON_DISCOVERY_AUDIT["existing_owner_audit"] == {
        "checked_on": "2026-07-23",
        "source_rows": 1067,
        "current_or_future_rows": 49,
        "returned_rows": 49,
        "owner_branch_count": 1,
        "data_pages_at_page_unit_100": 11,
        "empty_sentinel_page": 12,
        "required_list_requests": 24,
        "configured_owner": hongcheon.HONGCHEON_EXISTING_COURSE_PROVIDER,
        "configured_last_quality_rows": 10,
        "recommended_max_pages": 30,
        "recommended_detail_limit": 200,
        "two_run_live_equal": True,
        "source_catalogue_sha256": (
            "256dd01a53660f3b517103959b143cdc9a529fbdb24152b58be6ed61f054cbe2"
        ),
        "output_sha256": (
            "ee12971a97510fbcc2ff61dfc767518c131c637f688cecef49897bc183179de4"
        ),
    }


def test_only_exact_provider_and_canonical_url_are_accepted() -> None:
    assert hongcheon.is_hongcheon_education_target(_target())
    assert hongcheon.is_hongcheon_library_target(_target())
    assert not hongcheon.is_hongcheon_existing_course_target(_target())
    assert hongcheon.is_hongcheon_education_target(_f508_target())
    assert hongcheon.is_hongcheon_existing_course_target(_f508_target())
    assert not hongcheon.is_hongcheon_library_target(_f508_target())
    assert not hongcheon.is_hongcheon_education_target(
        Target(hongcheon.HONGCHEON_LIBRARY_PROVIDER + "_OTHER", hongcheon.HONGCHEON_LIBRARY_URL)
    )
    assert not hongcheon.is_hongcheon_education_target(
        Target(hongcheon.HONGCHEON_LIBRARY_PROVIDER, hongcheon.HONGCHEON_LIBRARY_URL + "#x")
    )
    assert not hongcheon.is_hongcheon_education_target(
        Target(hongcheon.HONGCHEON_LIBRARY_PROVIDER, hongcheon.HONGCHEON_LIBRARY_URL.replace("https://", "http://"))
    )
    assert not hongcheon.is_hongcheon_education_target(
        Target(
            hongcheon.HONGCHEON_EXISTING_COURSE_PROVIDER,
            hongcheon.HONGCHEON_EXISTING_COURSE_URL + "#x",
        )
    )
    assert not hongcheon.is_hongcheon_education_target(
        Target(
            hongcheon.HONGCHEON_LIBRARY_PROVIDER,
            hongcheon.HONGCHEON_EXISTING_COURSE_URL,
        )
    )


def test_public_url_allowlist_and_application_boundary_are_exact() -> None:
    assert hongcheon._guard_url(hongcheon.HONGCHEON_LIBRARY_URL) == "directory"
    assert (
        hongcheon._guard_url(hongcheon.hongcheon_existing_course_list_url(1))
        == "integrated_list"
    )
    assert (
        hongcheon._guard_url(hongcheon.hongcheon_existing_course_detail_url("4972"))
        == "integrated_detail"
    )
    for branch in hongcheon.HONGCHEON_LIBRARY_BRANCHES:
        assert hongcheon._guard_url(hongcheon.hongcheon_library_list_url(branch.site)) == "list"
        assert hongcheon._guard_url(
            hongcheon.hongcheon_library_list_url(branch.site, sentinel=True)
        ) == "sentinel"
        group_idx, category_idx, teach_idx = _identity(branch)
        assert hongcheon._guard_url(
            hongcheon.hongcheon_library_detail_url(
                branch.site, group_idx, category_idx, teach_idx
            )
        ) == "detail"

    forbidden = (
        "https://hongcheonlib.go.kr/yblib/module/teach/student/edit.do?teach_idx=101",
        "https://hongcheonlib.go.kr/yblib/module/teach/applyList.do?menu_idx=54",
        "https://hongcheonlib.go.kr/yblib/module/teach/download/h2/11/0/101.do",
        "https://hongcheonlib.go.kr/yblib/intro/login/index.do?menu_idx=56",
        hongcheon.HONGCHEON_LIBRARY_URL + "#fragment",
        hongcheon.HONGCHEON_LIBRARY_URL.replace("https://", "http://"),
        hongcheon.HONGCHEON_LIBRARY_URL.replace("https://", "https://user:pw@"),
        (
            "https://www.hongcheon.go.kr/edu/courseWebAppRegist.do?"
            "key=1196&course=4972"
        ),
        "https://www.hongcheon.go.kr/edu/login.do?key=1196",
        hongcheon.hongcheon_existing_course_list_url(1).replace(
            "pageUnit=100", "pageUnit=10"
        ),
        hongcheon.hongcheon_existing_course_detail_url("4972") + "#fragment",
    )
    for url in forbidden:
        with pytest.raises(hongcheon.HongcheonContractError):
            hongcheon._guard_url(url)
    with pytest.raises(hongcheon.HongcheonContractError):
        hongcheon._guard_url(hongcheon.HONGCHEON_LIBRARY_URL, "POST")


def test_tls_intermediate_fingerprint_and_verified_context_are_enforced() -> None:
    hongcheon.build_hongcheon_tls_context.cache_clear()
    context = hongcheon.build_hongcheon_tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    der = ssl.PEM_cert_to_DER_cert(hongcheon.HONGCHEON_SECTIGO_INTERMEDIATE_PEM)
    assert hashlib.sha256(der).hexdigest() == hongcheon.HONGCHEON_SECTIGO_INTERMEDIATE_SHA256


def test_tls_fingerprint_tampering_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    hongcheon.build_hongcheon_tls_context.cache_clear()
    monkeypatch.setattr(hongcheon, "HONGCHEON_SECTIGO_INTERMEDIATE_SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="fingerprint"):
        hongcheon.build_hongcheon_tls_context()
    hongcheon.build_hongcheon_tls_context.cache_clear()


def test_safe_session_keeps_dns_pinning_with_hongcheon_tls_adapter() -> None:
    session = SafeSession()
    try:
        hongcheon._prepare_session(session)
        adapter = session.get_adapter("https://hongcheonlib.go.kr/main/index.do")
        assert isinstance(adapter, hongcheon._HongcheonPinnedAdapter)
    finally:
        session.close()


def test_production_collection_requires_managed_session_factory() -> None:
    rows, parser, meta = hongcheon.collect(_target(), today="2026-07-23")
    assert rows == []
    assert parser == hongcheon.HONGCHEON_LIBRARY_PARSER
    assert meta["snapshot_complete"] is False
    assert meta["application_endpoints_called"] == 0
    assert "session_factory" in meta["configured_collection_error"]


def test_complete_six_branch_fixture_is_atomic_classified_and_pii_minimized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _fixture()
    original_get_text = Tag.get_text

    def guarded_get_text(self: Tag, *args: Any, **kwargs: Any) -> str:
        if "poison" in (self.get("class") or []):
            raise AssertionError("sensitive/free-form detail value was read")
        return original_get_text(self, *args, **kwargs)

    monkeypatch.setattr(Tag, "get_text", guarded_get_text)
    rows, parser, meta = hongcheon.collect(
        _target(),
        today="2026-07-23",
        session_factory=lambda: session,
        sleeper=lambda _seconds: None,
    )
    assert parser == hongcheon.HONGCHEON_LIBRARY_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 6
    assert meta["collected"] == 6
    assert meta["pages"] == meta["list_requests"] == 18
    assert meta["required_list_requests"] == 18
    assert meta["empty_sentinels"] == 6
    assert meta["catalogue_stability_rechecks"] == 6
    assert meta["directory_stability_rechecks"] == 1
    assert meta["detail_pages"] == 6
    assert meta["identity_bound_application_controls"] == 6
    assert meta["application_endpoints_called"] == 0
    assert meta["route_counts"] == {
        "directory": 2,
        "list": 12,
        "sentinel": 6,
        "detail": 6,
    }
    assert [row["branch"] for row in rows] == [
        branch.name for branch in hongcheon.HONGCHEON_LIBRARY_BRANCHES
    ]
    for row in rows:
        assert row["collection_category"] == "공공예약"
        assert row["domain_category"] == "교육·강좌"
        assert row["source_group"] == "municipal_reservation"
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["classification_locked"] is True
        assert row["municipality_code"] == "5172000000"
        assert row["status"] == "OPEN"
        assert row["reservation_available"] is True
        assert row["raw_fields"]["detail_verified"] is True
        assert row["raw_fields"]["application_control_verified"] is True
        assert row["raw_fields"]["application_endpoint_called"] is False
        serialized = repr(row)
        assert "010-1111-2222" not in serialized
        assert "033-430-9999" not in serialized
        assert "private@example.test" not in serialized
        with pytest.raises(hongcheon.HongcheonContractError):
            hongcheon._guard_url(row["application_url"])
    assert all(method == "GET" for method, _url, _kwargs in session.calls)
    assert not any("/student/" in url or "applyList" in url for _method, url, _kwargs in session.calls)


def test_complete_library_ledger_with_no_current_rows_is_successful_empty_snapshot() -> None:
    session = _fixture()
    rows, parser, meta = hongcheon.collect(
        _target(),
        today="2099-01-01",
        session_factory=lambda: session,
        sleeper=lambda _seconds: None,
    )
    assert parser == hongcheon.HONGCHEON_LIBRARY_PARSER
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"]
    assert meta["details_complete"] is True
    assert meta["route_counts"].get("detail", 0) == 0


@pytest.mark.parametrize(
    ("fixture_kwargs", "error_fragment"),
    [
        ({"sentinel_leak_site": "yblib"}, "empty sentinel contains records"),
        ({"unstable_site": "sslib"}, "catalogue changed during collection"),
        ({"detail_mismatch_site": "children"}, "detail title mismatch"),
        ({"directory_drift": True}, "official six-branch directory changed"),
        ({"detail_status": 500}, "public fetch failed"),
    ],
)
def test_any_boundary_or_detail_failure_suppresses_the_whole_snapshot(
    fixture_kwargs: dict[str, Any], error_fragment: str
) -> None:
    session = _fixture(**fixture_kwargs)
    rows, _parser, meta = hongcheon.collect(
        _target(),
        today="2026-07-23",
        session_factory=lambda: session,
        sleeper=lambda _seconds: None,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["collected"] == 0
    assert meta["application_endpoints_called"] == 0
    assert error_fragment in meta["error"]


def test_detail_limit_and_external_dedupe_fail_closed_without_partial_rows() -> None:
    session = _fixture()
    rows, _parser, meta = hongcheon.collect(
        _target(),
        today="2026-07-23",
        detail_limit=5,
        session_factory=lambda: session,
        sleeper=lambda _seconds: None,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "detail_limit" in meta["error"]
    assert meta["route_counts"].get("detail", 0) == 0

    session = _fixture()
    rows, _parser, meta = hongcheon.collect(
        _target(),
        today="2026-07-23",
        session_factory=lambda: session,
        sleeper=lambda _seconds: None,
        dedupe_fn=lambda values: values[:-1],
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "external dedupe" in meta["error"]


def test_max_pages_must_cover_every_catalogue_sentinel_and_stability_recheck() -> None:
    session = _fixture()
    rows, _parser, meta = hongcheon.collect(
        _target(),
        today="2026-07-23",
        max_pages=hongcheon.HONGCHEON_REQUIRED_LIST_REQUESTS - 1,
        session_factory=lambda: session,
        sleeper=lambda _seconds: None,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "17 is below required 18 catalogue/sentinel requests" in meta["error"]
    assert session.calls == []


def test_response_redirect_and_access_block_are_rejected_without_parsing() -> None:
    def redirect_route(url: str, _count: int) -> Response:
        return Response(url, text=_directory_html(), final_url=url + "?changed=1")

    rows, _parser, meta = hongcheon.collect(
        _target(),
        today="2026-07-23",
        session_factory=lambda: FakeSession(redirect_route),
        sleeper=lambda _seconds: None,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "public fetch failed" in meta["error"]


def test_f508_complete_1067_row_fixture_is_atomic_and_privacy_minimized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _f508_fixture()
    original_get_text = Tag.get_text

    def guarded_get_text(self: Tag, *args: Any, **kwargs: Any) -> str:
        if "poison" in (self.get("class") or []):
            raise AssertionError("sensitive/free-form detail value was read")
        return original_get_text(self, *args, **kwargs)

    monkeypatch.setattr(Tag, "get_text", guarded_get_text)
    rows, parser, meta = hongcheon.collect(
        _f508_target(),
        today="2026-07-23",
        max_pages=30,
        detail_limit=200,
        session_factory=lambda: session,
        sleeper=lambda _seconds: None,
    )

    assert parser == hongcheon.HONGCHEON_EXISTING_COURSE_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 1067
    assert meta["collected"] == 49
    assert meta["excluded_expired"] == 1018
    assert meta["page_size"] == 100
    assert meta["data_pages"] == 11
    assert meta["empty_sentinel_page"] == 12
    assert meta["pages"] == meta["list_requests"] == 24
    assert meta["required_list_requests"] == 24
    assert meta["pagination_complete"] is True
    assert meta["pagination_exhausted"] is True
    assert meta["source_cap_reached"] is False
    assert meta["catalogue_stability_rechecks"] == 12
    assert meta["detail_pages"] == 49
    assert meta["details_complete"] is True
    assert meta["identity_bound_application_controls"] == 3
    assert meta["application_endpoints_called"] == 0
    assert meta["route_counts"] == {
        "integrated_list": 24,
        "integrated_detail": 49,
    }
    assert meta["source_status_counts"] == {
        "온라인 접수중": 3,
        "온라인 방문접수 접수마감": 1,
        "방문접수 접수마감": 1,
        "온라인 접수마감": 1062,
    }
    assert len({row["provider_course_id"] for row in rows}) == 49
    assert rows[2]["capacity_current"] == 15
    assert rows[2]["capacity_total"] == 20
    assert rows[2]["target"] == "초등학생 3학년~6학년"
    assert rows[25]["target"] == ""
    assert rows[-1]["schedule_raw"] == rows[-1]["period"]
    for row in rows:
        assert row["provider"] == hongcheon.HONGCHEON_EXISTING_COURSE_PROVIDER
        assert row["branch"] == hongcheon.HONGCHEON_MUNICIPALITY_NAME
        assert row["collection_category"] == "공공예약"
        assert row["domain_category"] == "교육·강좌"
        assert row["source_group"] == "municipal_reservation"
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["classification_locked"] is True
        assert row["raw_fields"]["detail_verified"] is True
        assert row["raw_fields"]["application_endpoint_called"] is False
        serialized = repr(row)
        assert "이병규" not in serialized
        assert "개인강사" not in serialized
        assert "010-1111-2222" not in serialized
        assert "033-430-9999" not in serialized
        assert "private@example.test" not in serialized
        assert "jsessionid" not in serialized
        if row["application_url"]:
            assert row["application_url"].endswith(
                f"key=1196&course={row['provider_course_id'].rsplit(':', 1)[-1]}"
            )
            with pytest.raises(hongcheon.HongcheonContractError):
                hongcheon._guard_url(row["application_url"])
    assert all(method == "GET" for method, _url, _kwargs in session.calls)
    assert not any(
        "courseWebAppRegist.do" in url for _method, url, _kwargs in session.calls
    )


def test_f508_limits_are_checked_before_stability_or_detail_requests() -> None:
    session = _f508_fixture()
    rows, parser, meta = hongcheon.collect(
        _f508_target(),
        today="2026-07-23",
        max_pages=23,
        session_factory=lambda: session,
        sleeper=lambda _seconds: None,
    )
    assert rows == []
    assert parser == hongcheon.HONGCHEON_EXISTING_COURSE_PARSER
    assert meta["snapshot_complete"] is False
    assert "23 is below required 24 catalogue/sentinel requests" in meta["error"]
    assert meta["route_counts"] == {"integrated_list": 1}

    session = _f508_fixture()
    rows, _parser, meta = hongcheon.collect(
        _f508_target(),
        today="2026-07-23",
        max_pages=30,
        detail_limit=48,
        session_factory=lambda: session,
        sleeper=lambda _seconds: None,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "48 is below required 49 details" in meta["error"]
    assert meta["route_counts"] == {"integrated_list": 12}
    assert not any(
        urlparse(url).path == "/edu/courseWebView.do"
        for _method, url, _kwargs in session.calls
    )


@pytest.mark.parametrize(
    ("fixture_kwargs", "error_fragment"),
    [
        ({"sentinel_leak": True}, "empty sentinel contains records"),
        ({"unstable_page": 5}, "page 5 changed during collection"),
        ({"detail_mismatch_ordinal": 1}, "detail/list identity mismatch"),
        ({"application_mismatch_ordinal": 1}, "application identity changed"),
    ],
)
def test_f508_any_boundary_detail_or_application_failure_is_atomic(
    fixture_kwargs: dict[str, Any], error_fragment: str
) -> None:
    session = _f508_fixture(**fixture_kwargs)
    rows, parser, meta = hongcheon.collect(
        _f508_target(),
        today="2026-07-23",
        max_pages=30,
        detail_limit=200,
        session_factory=lambda: session,
        sleeper=lambda _seconds: None,
    )
    assert rows == []
    assert parser == hongcheon.HONGCHEON_EXISTING_COURSE_PARSER
    assert meta["snapshot_complete"] is False
    assert meta["collected"] == 0
    assert meta["application_endpoints_called"] == 0
    assert meta["configured_collection_error"] == meta["error"]
    assert error_fragment in meta["error"]


def test_f508_production_collection_requires_managed_session_factory() -> None:
    rows, parser, meta = hongcheon.collect(
        _f508_target(), today="2026-07-23", max_pages=30
    )
    assert rows == []
    assert parser == hongcheon.HONGCHEON_EXISTING_COURSE_PARSER
    assert meta["snapshot_complete"] is False
    assert meta["application_endpoints_called"] == 0
    assert "session_factory" in meta["configured_collection_error"]

    def blocked_route(url: str, _count: int) -> Response:
        return Response(url, text="<html><title>Access Denied</title></html>")

    rows, _parser, meta = hongcheon.collect(
        _target(),
        today="2026-07-23",
        session_factory=lambda: FakeSession(blocked_route),
        sleeper=lambda _seconds: None,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "public fetch failed" in meta["error"]


@pytest.mark.skipif(
    os.getenv("RUN_HONGCHEON_LIVE_AUDIT") != "1",
    reason="set RUN_HONGCHEON_LIVE_AUDIT=1 for the exact two-run live audit",
)
def test_live_source_is_complete_stable_and_identical_across_two_runs() -> None:
    snapshots: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    for _run in range(2):
        rows, parser, meta = hongcheon.collect(
            _target(),
            today="2026-07-23",
            allow_raw_requests_for_tests=True,
            timeout=30,
            max_requests=100,
        )
        assert parser == hongcheon.HONGCHEON_LIBRARY_PARSER
        assert meta["snapshot_complete"] is True
        assert meta["source_total"] == 303
        assert meta["collected"] == 15
        assert meta["branch_source_counts"] == hongcheon.HONGCHEON_DISCOVERY_AUDIT[
            "branch_source_counts"
        ]
        assert meta["branch_current_counts"] == hongcheon.HONGCHEON_DISCOVERY_AUDIT[
            "branch_current_counts"
        ]
        assert meta["detail_pages"] == 15
        assert meta["identity_bound_application_controls"] == 10
        assert meta["application_endpoints_called"] == 0
        assert meta["route_counts"] == {
            "directory": 2,
            "list": 12,
            "sentinel": 6,
            "detail": 15,
        }
        snapshots.append((rows, meta))
    first_rows, _first_meta = snapshots[0]
    second_rows, _second_meta = snapshots[1]
    def signature(values: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
        return [
            (
                row["provider_course_id"],
                row["title"],
                row["start_date"],
                row["end_date"],
                row["status"],
                row["branch"],
                bool(row["application_url"]),
            )
            for row in values
        ]

    assert signature(first_rows) == signature(second_rows)
    digest = hashlib.sha256(
        "\n".join(
            "|".join(map(str, item[:-1])) for item in signature(first_rows)
        ).encode()
    ).hexdigest()
    assert digest == "d37165079cb191947e69655ecc56a7f79508429a599b33d39d46ff97463f1b53"


@pytest.mark.skipif(
    os.getenv("RUN_HONGCHEON_F508_LIVE_AUDIT") != "1",
    reason="set RUN_HONGCHEON_F508_LIVE_AUDIT=1 for the F508 two-run audit",
)
def test_f508_live_source_is_complete_stable_and_identical_across_two_runs() -> None:
    snapshots: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    for _run in range(2):
        rows, parser, meta = hongcheon.collect(
            _f508_target(),
            today="2026-07-23",
            max_pages=30,
            detail_limit=200,
            max_requests=250,
            allow_raw_requests_for_tests=True,
            timeout=30,
        )
        assert parser == hongcheon.HONGCHEON_EXISTING_COURSE_PARSER
        assert meta["snapshot_complete"] is True, meta
        assert meta["source_total"] == 1067
        assert meta["collected"] == 49
        assert meta["excluded_expired"] == 1018
        assert meta["data_pages"] == 11
        assert meta["empty_sentinel_page"] == 12
        assert meta["pages"] == 24
        assert meta["detail_pages"] == 49
        assert meta["identity_bound_application_controls"] == 3
        assert meta["application_endpoints_called"] == 0
        assert meta["route_counts"] == {
            "integrated_list": 24,
            "integrated_detail": 49,
        }
        snapshots.append((rows, meta))

    first_rows, first_meta = snapshots[0]
    second_rows, second_meta = snapshots[1]
    assert hongcheon._f508_output_signature(first_rows) == (
        hongcheon._f508_output_signature(second_rows)
    )
    assert first_meta["source_catalogue_sha256"] == second_meta[
        "source_catalogue_sha256"
    ]
    assert first_meta["output_sha256"] == second_meta["output_sha256"]
