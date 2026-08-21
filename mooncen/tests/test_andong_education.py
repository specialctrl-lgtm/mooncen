from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
import os
from threading import Lock
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_andong as andong


@dataclass(frozen=True)
class Course:
    identity: str
    edu_type: str
    owner: str
    title: str
    source_status: str
    detail_status: str
    list_period: str = ""
    detail_period: str = ""
    schedule: str = "10:00 ~ 12:00"
    category_code: str = ""
    selected_url: str = ""
    visible_apply: bool = False
    category: str = "인문교육"
    target: str = "안동시민"
    venue: str = "안동 교육실"
    capacity: str = "10명"
    apply_period: str = "2026-07-01 ~ 2026-07-20"


@dataclass(frozen=True)
class LibraryCourse:
    group_idx: str
    teach_idx: str
    branch_code: str
    branch_short: str
    category: str
    list_title: str
    detail_title: str
    status: str
    period: str
    schedule: str
    target: str
    venue: str
    current: int
    capacity: int
    wait_current: int
    wait_capacity: int
    apply_period: str


class Response:
    def __init__(self, url: str, html: str, status_code: int = 200) -> None:
        self.url = url
        self.status_code = status_code
        self.headers: dict[str, str] = {"Content-Type": "text/html;charset=UTF-8"}
        self.content = html.encode("utf-8")


class DummySession:
    def close(self) -> None:
        return None


def _integrated_courses() -> list[Course]:
    archived = [
        Course(
            identity=str(1000 + offset),
            edu_type="1",
            owner="학습관교육",
            title=("[주간교육] 지난 체험" if offset == 0 else f"[주간교육] 지난 교육 {offset}"),
            source_status="마감",
            detail_status="접수마감",
            list_period="2026-01-01 ~ 2026-06-30",
            detail_period="2026-01-01 ~ 2026-06-30",
            category_code="10",
        )
        for offset in range(24)
    ]
    return archived + [
        Course(
            identity="2001",
            edu_type="6",
            owner="가톨릭상지대학교",
            title="목공 전문가 과정",
            source_status="접수중",
            detail_status="접수중",
            list_period="2026-08-01 ~ 2026-09-01",
            detail_period="2026-08-01 ~ 2026-09-01",
            selected_url="https://lead.csj.ac.kr",
            visible_apply=True,
            venue="창의관 (054-000-0000)",
        ),
        Course(
            identity="2002",
            edu_type="1",
            owner="학습관교육",
            title="[주간교육] 미래 수채화",
            source_status="대기",
            detail_status="접수대기",
            list_period="2026-09-01 ~ 2026-12-01",
            detail_period="2026-09-01 ~ 2026-12-01",
            category_code="10",
            capacity="12명 / 6명",
        ),
        Course(
            identity="2003",
            edu_type="2",
            owner="길거리교실",
            title="동네 역사 읽기",
            source_status="대기",
            detail_status="대기",
            detail_period="신청자 자유",
            apply_period="상시",
            capacity="5명이상",
        ),
        Course(
            identity="2004",
            edu_type="6",
            owner="가톨릭상지대학교",
            title="도시 텃밭 체험",
            source_status="마감",
            detail_status="접수마감",
            list_period="2026-07-25 ~ 2026-08-01",
            detail_period="2026-07-25 ~ 2026-08-01",
            selected_url="https://lead.csj.ac.kr",
        ),
        Course(
            identity="2005",
            edu_type="3",
            owner="시민강사",
            title="지난 시민 회화",
            source_status="수업가능",
            detail_status="수업가능",
            detail_period="2026-06-01 ~ 2026-06-30",
            visible_apply=True,
        ),
    ]


def _filter_html(*, drift: bool = False) -> str:
    type_options = (
        '<option value="0">전체</option><option value="1">학습관교육</option>'
        '<option value="2">길거리교실</option><option value="3">시민강사</option>'
        '<option value="4">명사초청</option>'
    )
    org_options = '<option value="9999">전체</option><option value="80">가톨릭상지대학교</option>'
    registries = {
        "typeList": ["10", "20", "30", "40", "50", "60", "70", "80", "90", "100", "111", "160", "300"],
        "lectureTimeList": ["1", "2", "3", "4", "5"],
        "eduDayList": ["1", "2", "3", "4", "5", "6", "7"],
        "eduGroupList": ["1", "2", "3", "4", "5", "6", "7"],
        "costTypeList": ["P", "F"],
        "recruitmentTypeList": ["1", "2"],
        "stateList": ["1", "2", "3", "4"],
    }
    if drift:
        registries["stateList"] = ["1", "2", "3"]
    inputs: list[str] = []
    for name, values in registries.items():
        for value in values:
            identity = f"{name}_{value}"
            inputs.append(
                f'<input id="{identity}" name="{name}" value="{value}"><label for="{identity}">{name} {value}</label>'
            )
    return (
        f'<select name="eduType">{type_options}</select>'
        f'<select name="orgIdx">{org_options}</select>'
        + "".join(inputs)
        + '<select name="recordCountPerPage"><option value="12">12</option>'
        '<option value="16">16</option><option value="24" selected>24</option></select>'
    )


def _list_card(course: Course, *, title_suffix: str = "") -> str:
    if course.list_period:
        details = (
            f"<li><strong>교육기간</strong>{escape(course.list_period)}</li>"
            f"<li><strong>교육시간</strong>{escape(course.schedule)}</li>"
        )
    else:
        details = "<li><strong>강사명</strong>비공개 강사</li>"
    return (
        "<li>"
        f'<a href="javascript:void(0);" onclick="fn_popup_open_totalLecture({course.identity},{course.edu_type},\'Y\');">'
        f'<p class="title"><span>{escape(course.owner)}</span>{escape(course.title + title_suffix)}</p>'
        f'<ul class="detail">{details}</ul>'
        f'<div class="state"><span>{escape(course.source_status)}</span></div>'
        "</a></li>"
    )


def _integrated_list_html(
    page: int,
    courses: list[Course],
    *,
    sentinel_nonempty: bool = False,
    drift_registry: bool = False,
    title_suffix: str = "",
) -> str:
    total, last = len(courses), 2
    if page == 1:
        selected = courses[:24]
    elif page == 2:
        selected = courses[24:]
    else:
        selected = courses[:1] if sentinel_nonempty else []
    body = "".join(_list_card(course, title_suffix=title_suffix) for course in selected)
    if not selected:
        body = "<li>등록된 데이터가 없습니다.</li>"
    return (
        "<html><body>"
        f'<div class="page-num"><span class="total">전체 {total} 건 ({page}/{last})</span></div>'
        f'<input name="currentPageNo" value="{page}">'
        f"{_filter_html(drift=drift_registry)}"
        f'<ul class="search-list">{body}</ul>'
        "</body></html>"
    )


def _detail_owner(course: Course) -> str:
    return {"1": "학습관", "2": "길거리 교실", "3": "시민강사", "4": "명사초청"}.get(course.edu_type, course.owner)


def _integrated_detail_html(
    course: Course,
    *,
    wrong_hidden: bool = False,
    missing_apply: bool = False,
    pii_target: bool = False,
    title_drift: bool = False,
) -> str:
    target = "문의 054-111-2222" if pii_target else course.target
    title = course.title + (" 변경" if title_drift else "")
    fields = [
        ("교육분야", course.category),
        ("교육대상", target),
        ("교육기간", course.detail_period),
        ("교육시간", course.schedule),
        ("수강료", "무료"),
        ("재료비(기타비용)", "없음"),
        ("교육장소", course.venue),
        ("모집형태", "선착순"),
        ("1차접수기간", course.apply_period),
        ("모집정원", course.capacity),
        ("교육내용", "상세 자유문과 admin@example.com"),
        ("강의계획서", "개인 첨부파일"),
        ("문의처", "담당자 054-123-4567"),
    ]
    table_rows = "".join(f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>" for label, value in fields)
    apply = ""
    if course.visible_apply and not missing_apply:
        apply = '<a href="#" onclick="moveWrite();">신청하기</a>'
    return (
        "<html><body>"
        '<div class="pop-tit">'
        f'<span id="eduTypeText">{escape(_detail_owner(course))}</span>'
        f'<span id="nameText">{escape(title)}</span>'
        f'<div class="state"><span>{escape(course.detail_status)}</span></div></div>'
        f'<input id="selectedUrl" name="selectedUrl" value="{escape(course.selected_url)}">'
        f'<input id="selectedIdx" name="idx" value="{"9999" if wrong_hidden else course.identity}">'
        f'<input id="selectedEduType" name="selectedEduType" value="{course.edu_type}">'
        f'<input id="selectedCategory" name="selectedCategory" value="{course.category_code}">'
        '<input id="searchYn" name="searchYn" value="N">'
        f'<div class="pop-con"><table class="tbl Thead">{table_rows}</table></div>'
        f'<div class="btn-box">{apply}<a href="#" onclick="fn_popup_close_totalLecture(this);">닫기</a></div>'
        "</body></html>"
    )


class IntegratedFetcher:
    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.courses = _integrated_courses()
        self.calls: list[str] = []
        self.page_calls: Counter[int] = Counter()
        self.lock = Lock()

    def __call__(self, _session: object, url: str, _timeout: int) -> Response:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        with self.lock:
            self.calls.append(url)
        if parsed.path == andong.ANDONG_LIST_PATH:
            page = int(query["currentPageNo"][0])
            with self.lock:
                self.page_calls[page] += 1
                call_number = self.page_calls[page]
            html = _integrated_list_html(
                page,
                self.courses,
                sentinel_nonempty=self.mode == "sentinel_nonempty" and page == 3,
                drift_registry=self.mode == "registry_drift" and page == 2,
                title_suffix=" 변경" if self.mode == "boundary_drift" and page == 1 and call_number > 1 else "",
            )
            return Response(url, html)
        identity = query["idx"][0]
        edu_type = query["eduType"][0]
        course = next(course for course in self.courses if course.identity == identity and course.edu_type == edu_type)
        if self.mode == "detail_failure" and identity == "2002":
            return Response(url, "<html>failed</html>", 500)
        return Response(
            url,
            _integrated_detail_html(
                course,
                wrong_hidden=self.mode == "wrong_hidden" and identity == "2001",
                missing_apply=self.mode == "missing_apply" and identity == "2001",
                pii_target=self.mode == "pii_target" and identity == "2001",
                title_drift=self.mode == "title_drift" and identity == "2001",
            ),
        )


def _integrated_target() -> dict[str, str]:
    return {"provider": andong.ANDONG_PROVIDER, "url": andong.ANDONG_CANONICAL_URL}


def _library_courses() -> list[LibraryCourse]:
    return [
        LibraryCourse(
            "144",
            "833",
            "0002",
            "어린이",
            "기타",
            "모래가 들려주는 이야기",
            "모래가 들려주는 이야기",
            "신청대기",
            "2026-08-12 ~ 2026-08-12",
            "19:00 ~ 19:50",
            "전체",
            "어린이도서관 3층 다목적실",
            0,
            90,
            0,
            20,
            "2026-08-04 10:00 ~ 2026-08-07 18:00",
        ),
        LibraryCourse(
            "152",
            "832",
            "0003",
            "중앙",
            "여름독서교실",
            "책장을 열면 과학이 팡!",
            "책장을 열면 과학이 팡!",
            "신청대기",
            "2026-08-04 ~ 2026-08-07",
            "09:10 ~ 12:00",
            "초등 1학년~3학년",
            "중앙도서관 1강의실",
            0,
            16,
            0,
            5,
            "2026-07-27 10:00 ~ 2026-07-31 18:00",
        ),
        LibraryCourse(
            "153",
            "831",
            "0002",
            "어린이",
            "독서교실",
            "미래를 여는 경제 탐험대(초등학교3~6학년)",
            "미래를 여는 경제 탐험대(초등학교3~6학년)",
            "신청대기",
            "2026-08-04 ~ 2026-08-07",
            "14:00 ~ 16:50",
            "초등학교3~6학년",
            "2층 2강의실",
            0,
            12,
            0,
            5,
            "2026-07-27 10:00 ~ 2026-07-31 18:00",
        ),
        LibraryCourse(
            "165",
            "830",
            "0001",
            "웅부",
            "문화가 있는 날",
            "감각적인 여름 라탄백 만들기 원데이 클래스",
            "감각적인 여름 라탄백 만들기 원데이 클래스",
            "정원마감",
            "2026-07-29 ~ 2026-07-29",
            "14:00 ~ 16:00",
            "안동시민",
            "3강의실",
            20,
            20,
            5,
            5,
            "2026-07-21 10:00 ~ 2026-07-23 18:00",
        ),
        LibraryCourse(
            "147",
            "205",
            "0003",
            "중앙",
            "기타",
            "[기타] 안동시립중앙도서관 행사 문자 안내 희...",
            "[기타] 안동시립중앙도서관 행사 문자 안내 희망",
            "접수하기",
            "2021-07-01 ~ 2040-04-01",
            "09:00 ~ 18:00",
            "안동시민",
            "안동시립중앙도서관",
            1225,
            10000,
            0,
            0,
            "2021-07-01 09:00 ~ 2040-04-01 18:00",
        ),
        LibraryCourse(
            "144",
            "25",
            "0002",
            "어린이",
            "기타",
            "[기타]안동시립어린이도서관 행사 및 문화교실 ...",
            "[기타]안동시립어린이도서관 행사 및 문화교실 문자 안내 희망",
            "접수하기",
            "2018-04-01 ~ 2040-04-01",
            "09:00 ~ 18:00",
            "전체",
            "안동시립어린이도서관",
            618,
            1000,
            0,
            0,
            "2018-04-01 09:00 ~ 2040-04-01 18:00",
        ),
        LibraryCourse(
            "141",
            "24",
            "0001",
            "웅부",
            "기타",
            "[기타]안동시립웅부도서관 행사 및 문화교실 문...",
            "[기타]안동시립웅부도서관 행사 및 문화교실 문자 안내 희망",
            "접수하기",
            "2018-04-01 ~ 2041-04-01",
            "09:00 ~ 18:00",
            "전체",
            "안동시립웅부도서관",
            533,
            1000,
            0,
            0,
            "2018-04-01 09:00 ~ 2040-04-01 18:00",
        ),
    ]


def _library_form(menu_idx: str, category: str) -> str:
    return (
        '<form id="teach" action="/andonglibrary/module/teach/student/save.do" method="POST">'
        '<input type="hidden" name="group_idx" value="0">'
        '<input type="hidden" name="teach_idx" value="0">'
        f'<input type="hidden" name="menu_idx" value="{menu_idx}">'
        '<input type="hidden" name="category_idx" value="0">'
        f'<input type="hidden" name="searchCate1" value="{category}">'
        '<input type="hidden" name="large_category_idx" value="0">'
        '<input type="hidden" name="category_idx" value="0">'
        '<select id="org_code" name="org_code"><option value="">전체</option>'
        '<option value="0000">통합</option><option value="0003">중앙</option>'
        '<option value="0001">웅부</option><option value="0002">어린이</option></select>'
        "</form>"
    )


def _library_row(course: LibraryCourse, *, title_suffix: str = "", unknown_category: bool = False) -> str:
    category = "미분류" if unknown_category and course.teach_idx == "832" else course.category
    status = course.status
    if status in {"접수하기", "대기자신청"}:
        apply_status = "2" if status == "대기자신청" else "1"
        control = (
            '<a href="" class="btn btn1 add" keyvalue1="h12" '
            f'keyvalue2="{course.group_idx}" keyvalue3="0" keyvalue4="{course.teach_idx}" '
            f'keyvalue5="23" apply_status="{apply_status}">{status}</a>'
        )
    else:
        control = f'<a href="javascript:void(0);" class="btn">{status}</a>'
    return (
        "<tr>"
        f'<td class="list_cate center"><span class="codeName phlib{course.branch_code}">{course.branch_short}</span>'
        f'<span class="moBr">{escape(category)}</span></td>'
        f'<td class="list_cate center webBr">{escape(category)}</td>'
        '<td class="title"><dl><dt class="title">'
        f'<a class="detail-btn" href="#" keyvalue1="{course.group_idx}" keyvalue2="0" '
        f'keyvalue3="{course.teach_idx}">{escape(course.list_title + title_suffix)}</a></dt>'
        f'<dd class="con">대상 : {escape(course.target)} {course.capacity}명</dd>'
        f'<dd class="con">장소 : {escape(course.venue)}</dd></dl></td>'
        f'<td class="center">인터넷접수 <span>{course.current} / {course.capacity}</span></td>'
        f'<td class="visit center webBr">{course.current} / {course.capacity}<br>{course.wait_current} / {course.wait_capacity}</td>'
        f'<td class="center">{escape(course.period)}<br>{escape(course.schedule)} (수)</td>'
        f'<td class="center">{control}<a href="#usercheck" class="btn" onclick="userCheckQr();">출입확인</a></td>'
        "</tr>"
    )


def _library_list_html(
    menu_idx: str,
    category: str,
    courses: list[LibraryCourse],
    *,
    title_suffix: str = "",
    unknown_category: bool = False,
) -> str:
    rows = "".join(
        _library_row(course, title_suffix=title_suffix, unknown_category=unknown_category) for course in courses
    )
    return (
        "<html><body>"
        f"{_library_form(menu_idx, category)}"
        '<table class="list01"><thead><tr>'
        "<th>도서관명 <span>·</span><span>분류</span></th><th>분류</th><th>제목</th>"
        "<th>모집 방법 <span>·</span><span>정원</span></th><th>정원</th><th>행사기간</th><th>접수 상태</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
        "</body></html>"
    )


def _library_detail_html(
    course: LibraryCourse,
    *,
    wrong_identity: bool = False,
    missing_apply: bool = False,
    pii_target: bool = False,
    unknown_category: bool = False,
) -> str:
    category = "미분류" if unknown_category and course.teach_idx == "832" else course.category
    target = "문의 054-123-4567" if pii_target else course.target
    fields = [
        ("기관", course.branch_short),
        ("행사 분류", category),
        ("행사 설명", "자유로운 상세 설명"),
        ("강사명", "개인 강사"),
        ("준비물 및 재료비", "무료"),
        ("참가비", "무료"),
        ("행사기간(*)", course.period),
        ("행사기간", course.period),
        ("행사시간", course.schedule),
        ("행사요일", "수"),
        ("행사장소", course.venue),
        ("행사대상", target),
        ("행사내용", "개인정보 가능 첨부 본문"),
        ("접수기간", course.apply_period),
        ("현재 참여 / 모집", f"{course.current} 명 / {course.capacity} 명"),
        ("담당부서 및 전화번호", "담당자 / 054-123-4567"),
    ]
    table = "".join(f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>" for label, value in fields)
    table += "<tr><th>현재 모집 상세현황</th></tr>"
    if course.status in {"접수하기", "대기자신청"} and not missing_apply:
        teach_idx = "9999" if wrong_identity else course.teach_idx
        apply_status = "2" if course.status == "대기자신청" else "1"
        label = "대기자신청" if course.status == "대기자신청" else "수강신청"
        control = (
            '<a href="" class="btn btn1 add" keyvalue1="h12" '
            f'keyvalue2="{course.group_idx}" keyvalue3="0" keyvalue4="{teach_idx}" '
            f'keyvalue5="0" apply_status="{apply_status}">{label}</a>'
        )
    elif course.status == "신청대기":
        control = '<a href="javascript:void(0);" class="btn">신청대기</a>'
    elif course.status == "정원마감":
        control = '<a href="javascript:void(0);" class="btn">정원마감</a>'
    else:
        control = ""
    return (
        "<html><body>"
        f'<div class="teach_top"><h3>{escape(course.detail_title)}</h3></div>'
        f'<table class="tstyle nohead">{table}</table>{control}'
        '<a id="back-btn" href="" class="btn btn1">목록으로</a>'
        "</body></html>"
    )


class LibraryFetcher:
    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.courses = _library_courses()
        self.calls: list[str] = []
        self.list_calls: Counter[str] = Counter()
        self.lock = Lock()

    def __call__(self, _session: object, url: str, _timeout: int) -> Response:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        with self.lock:
            self.calls.append(url)
        if parsed.path == andong.ANDONG_LIBRARY_LIST_PATH:
            menu_idx = query["menu_idx"][0]
            category = query["searchCate1"][0]
            with self.lock:
                self.list_calls[menu_idx] += 1
                call_number = self.list_calls[menu_idx]
            courses = [] if menu_idx == "362" else self.courses
            return Response(
                url,
                _library_list_html(
                    menu_idx,
                    category,
                    courses,
                    title_suffix=" 변경" if self.mode == "boundary_drift" and call_number > 1 else "",
                    unknown_category=self.mode == "unknown_category",
                ),
            )
        teach_idx = query["teach_idx"][0]
        course = next(course for course in self.courses if course.teach_idx == teach_idx)
        if self.mode == "detail_failure" and teach_idx == "832":
            return Response(url, "<html>failed</html>", 500)
        return Response(
            url,
            _library_detail_html(
                course,
                wrong_identity=self.mode == "wrong_identity" and teach_idx == "205",
                missing_apply=self.mode == "missing_apply" and teach_idx == "205",
                pii_target=self.mode == "pii_target" and teach_idx == "832",
                unknown_category=self.mode == "unknown_category",
            ),
        )


def _library_target(*, culture: bool = False) -> dict[str, str]:
    return {
        "provider": (andong.ANDONG_LIBRARY_CULTURE_PROVIDER if culture else andong.ANDONG_LIBRARY_EVENT_PROVIDER),
        "url": andong.ANDONG_LIBRARY_CULTURE_URL if culture else andong.ANDONG_LIBRARY_EVENT_URL,
    }


def _collect_integrated(fetcher: IntegratedFetcher, **kwargs: object):
    return andong.collect_andong_education(
        _integrated_target(),
        today="2026-07-22",
        session_factory=DummySession,
        fetcher=fetcher,
        **kwargs,
    )


def _collect_library(fetcher: LibraryFetcher, *, culture: bool = False, **kwargs: object):
    return andong.collect_andong_education(
        _library_target(culture=culture),
        today="2026-07-22",
        session_factory=DummySession,
        fetcher=fetcher,
        **kwargs,
    )


def test_strict_target_matcher_accepts_only_three_canonical_owners() -> None:
    assert andong.is_andong_education_target(_integrated_target())
    assert andong.is_andong_education_target(_library_target(culture=True))
    assert andong.is_andong_education_target(_library_target())
    assert not andong.is_andong_education_target(
        {"provider": andong.ANDONG_PROVIDER, "url": andong.ANDONG_REVIEW_FILTER_URL}
    )
    assert not andong.is_andong_education_target(
        {
            "provider": andong.ANDONG_LIBRARY_EVENT_PROVIDER,
            "url": andong.ANDONG_LIBRARY_EVENT_URL + "&org_code=0003",
        }
    )
    assert not andong.is_andong_education_target(
        {"provider": andong.ANDONG_LIBRARY_EVENT_PROVIDER, "url": andong.ANDONG_LIBRARY_CULTURE_URL}
    )


def test_integrated_full_boundary_current_partition_and_exact_rows() -> None:
    fetcher = IntegratedFetcher()
    rows, parser, meta = _collect_integrated(fetcher)

    assert parser == andong.ANDONG_PARSER
    assert len(rows) == 4
    assert meta["source_total"] == meta["source_rows"] == 29
    assert meta["data_pages"] == 2
    assert meta["post_last_empty_page"] == 3
    assert meta["list_requests"] == meta["required_list_requests"] == 5
    assert meta["boundary_rechecks"] == 2
    assert meta["fixed_date_current_count"] == 3
    assert meta["status_detail_candidate_count"] == 2
    assert meta["current_candidate_count"] == meta["detail_attempts"] == meta["detail_pages"] == 5
    assert meta["archived_rows_skipped_before_detail"] == 24
    assert meta["current_source_count"] == 4
    assert meta["evergreen_current_count"] == 1
    assert meta["experience_count"] == 1
    assert meta["experience_excluded_count"] == 0
    assert meta["detail_inactive_or_invalid_count"] == 1
    assert meta["branch_counts"] == {
        "가톨릭상지대학교": 2,
        "길거리교실": 1,
        "안동시 평생학습관": 1,
    }
    assert meta["status_counts"] == {"OPEN": 1, "SCHEDULED": 2, "CLOSED": 1}
    assert meta["domain_category_counts"] == {"교육·강좌": 3, "체험·견학": 1}
    assert meta["service_group_counts"] == {"공공강좌": 3, "체험": 1}
    assert meta["application_control_count"] == 1
    assert meta["snapshot_complete"] is meta["full_snapshot_validated"] is True
    assert not meta["configured_collection_error"]
    assert {row["provider_course_id"] for row in rows} == {
        f"{andong.ANDONG_PROVIDER}:6:2001",
        f"{andong.ANDONG_PROVIDER}:1:2002",
        f"{andong.ANDONG_PROVIDER}:2:2003",
        f"{andong.ANDONG_PROVIDER}:6:2004",
    }


def test_integrated_application_identity_privacy_and_evergreen_contract() -> None:
    fetcher = IntegratedFetcher()
    rows, _parser, meta = _collect_integrated(fetcher)
    by_id = {row["raw_fields"]["identity"]: row for row in rows}

    assert by_id["2001"]["application_url"] == "https://lead.csj.ac.kr"
    assert by_id["2001"]["reservation_available"] is True
    assert by_id["2001"]["venue"] == "창의관"
    assert by_id["2002"]["branch_code"] == "ANDONG_LIFELONG_CENTER"
    assert by_id["2002"]["waitlist_capacity"] == 6
    assert by_id["2003"]["start_date"] == by_id["2003"]["end_date"] == ""
    assert by_id["2003"]["raw_fields"]["current_basis"] == "on_demand_evergreen"
    assert by_id["2004"]["program_type"] == "체험"
    assert by_id["2004"]["domain_category"] == "체험·견학"
    assert by_id["2004"]["service_group"] == "체험"
    assert all(row["description"] == row["title"] for row in rows)
    assert all(not (set(row) & {"instructor", "contact", "attachments", "detail_description"}) for row in rows)
    assert "054-123-4567" not in repr(rows)
    assert "admin@example.com" not in repr(rows)
    assert meta["pii_payload_persisted"] is False
    assert meta["forbidden_applicant_endpoint_requests"] == 0
    assert all("receipt/write.do" not in url and "student/edit.do" not in url for url in fetcher.calls)


@pytest.mark.parametrize(
    "mode",
    [
        "sentinel_nonempty",
        "registry_drift",
        "boundary_drift",
        "detail_failure",
        "wrong_hidden",
        "missing_apply",
        "pii_target",
        "title_drift",
    ],
)
def test_integrated_contract_drift_is_atomic(mode: str) -> None:
    rows, _parser, meta = _collect_integrated(IntegratedFetcher(mode))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["full_snapshot_validated"] is False
    assert meta["configured_collection_error"]


def test_integrated_detail_cap_and_dedupe_are_atomic() -> None:
    rows, _parser, meta = _collect_integrated(IntegratedFetcher(), detail_limit=4)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit" in meta["configured_collection_error"]

    rows, _parser, meta = _collect_integrated(IntegratedFetcher(), dedupe_rows=lambda incoming: incoming[:-1])
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "cardinality" in meta["configured_collection_error"]


def test_library_culture_ledger_exact_empty_is_complete_not_failure() -> None:
    rows, parser, meta = _collect_library(LibraryFetcher(), culture=True)
    assert parser == andong.ANDONG_LIBRARY_PARSER
    assert rows == []
    assert meta["owner_provider"] == andong.ANDONG_LIBRARY_CULTURE_PROVIDER
    assert meta["source_total"] == meta["current_candidate_count"] == meta["detail_attempts"] == 0
    assert meta["list_requests"] == meta["required_list_requests"] == 2
    assert meta["boundary_rechecks"] == 1
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert not meta["configured_collection_error"]


def test_library_event_owner_collects_three_nonduplicate_education_rows() -> None:
    fetcher = LibraryFetcher()
    rows, parser, meta = _collect_library(fetcher)

    assert parser == andong.ANDONG_LIBRARY_PARSER
    assert meta["source_total"] == meta["source_rows"] == 7
    assert meta["current_candidate_count"] == meta["detail_attempts"] == meta["detail_pages"] == 7
    assert meta["education_scope_excluded_count"] == 4
    assert meta["detail_exclusion_counts"] == {
        "performance": 1,
        "notification_subscription": 3,
    }
    assert meta["returned_count"] == len(rows) == 3
    assert meta["branch_counts"] == {
        "안동시립어린이도서관": 1,
        "안동시립웅부도서관": 1,
        "안동시립중앙도서관": 1,
    }
    assert meta["status_counts"] == {"SCHEDULED": 2, "CLOSED": 1}
    assert meta["application_control_count"] == 0
    assert meta["integrated_identity_overlap_count"] == 0
    assert meta["separate_from_integrated_owner"] is True
    assert meta["snapshot_complete"] is meta["full_snapshot_validated"] is True
    assert {row["title"] for row in rows} == {
        "책장을 열면 과학이 팡!",
        "미래를 여는 경제 탐험대(초등학교3~6학년)",
        "감각적인 여름 라탄백 만들기 원데이 클래스",
    }
    assert any(row["category"] == "여름독서교실" for row in rows)
    assert {row["provider"] for row in rows} == {andong.ANDONG_LIBRARY_EVENT_PROVIDER}
    assert all(row["provider_course_id"].startswith(f"{andong.ANDONG_LIBRARY_EVENT_PROVIDER}:23:") for row in rows)


def test_library_application_identity_and_pii_are_validated_but_not_requested_or_persisted() -> None:
    fetcher = LibraryFetcher()
    rows, _parser, meta = _collect_library(fetcher)
    assert all(row["description"] == row["title"] for row in rows)
    assert all(not row["reservation_available"] for row in rows)
    assert "개인 강사" not in repr(rows)
    assert "054-123-4567" not in repr(rows)
    assert "개인정보 가능 첨부 본문" not in repr(rows)
    assert meta["pii_payload_persisted"] is False
    assert meta["forbidden_applicant_endpoint_requests"] == 0
    assert all("student/edit.do" not in url and "student/save.do" not in url for url in fetcher.calls)


@pytest.mark.parametrize(
    "mode",
    [
        "boundary_drift",
        "detail_failure",
        "wrong_identity",
        "missing_apply",
        "pii_target",
        "unknown_category",
    ],
)
def test_library_contract_drift_is_atomic(mode: str) -> None:
    rows, _parser, meta = _collect_library(LibraryFetcher(mode))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["full_snapshot_validated"] is False
    assert meta["configured_collection_error"]


def test_library_detail_cap_and_dedupe_are_atomic() -> None:
    rows, _parser, meta = _collect_library(LibraryFetcher(), detail_limit=6)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit" in meta["configured_collection_error"]

    rows, _parser, meta = _collect_library(LibraryFetcher(), dedupe_rows=lambda incoming: incoming[:-1])
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "cardinality" in meta["configured_collection_error"]


@pytest.mark.skipif(os.getenv("RUN_LIVE_MUNICIPAL") != "1", reason="set RUN_LIVE_MUNICIPAL=1")
def test_live_andong_integrated_and_library_contracts() -> None:
    rows, _parser, meta = andong.collect_andong_education(_integrated_target(), today="2026-07-22")
    assert len(rows) == meta["returned_count"] == 110
    assert meta["source_total"] == 3152
    assert meta["data_pages"] == 132
    assert meta["current_candidate_count"] == meta["detail_pages"] == 115
    assert meta["current_source_count"] == 110
    assert meta["evergreen_current_count"] == 28
    assert meta["experience_count"] == 1
    assert meta["experience_excluded_count"] == 0
    assert meta["branch_counts"] == {
        "가톨릭상지대학교": 26,
        "길거리교실": 28,
        "시민강사": 4,
        "안동시 평생학습관": 52,
    }
    assert meta["status_counts"] == {"OPEN": 4, "SCHEDULED": 82, "CLOSED": 24}
    assert meta["snapshot_complete"] is True

    culture_rows, _parser, culture_meta = andong.collect_andong_education(
        _library_target(culture=True), today="2026-07-22"
    )
    assert culture_rows == []
    assert culture_meta["source_total"] == 0
    assert culture_meta["snapshot_complete"] is True

    library_rows, _parser, library_meta = andong.collect_andong_education(
        _library_target(), today="2026-07-22"
    )
    assert len(library_rows) == library_meta["returned_count"] == 3
    assert library_meta["source_total"] == library_meta["detail_pages"] == 7
    assert library_meta["detail_exclusion_counts"] == {
        "performance": 1,
        "notification_subscription": 3,
    }
    assert library_meta["branch_counts"] == {
        "안동시립어린이도서관": 1,
        "안동시립웅부도서관": 1,
        "안동시립중앙도서관": 1,
    }
    assert library_meta["status_counts"] == {"SCHEDULED": 2, "CLOSED": 1}
    assert library_meta["snapshot_complete"] is True


def test_library_waitlist_application_is_bound_and_collected() -> None:
    course = replace(_library_courses()[1], status="대기자신청")
    ledger = andong._parse_library_ledger(
        BeautifulSoup(_library_list_html("368", "23", [course]), "html.parser"),
        "368",
        "23",
    )
    listed = ledger.rows[0]
    assert listed["status"] == "WAITING"
    assert listed["list_application_control"] is True

    detail_html = _library_detail_html(course).replace(
        "<tbody>",
        (
            '<tbody><tr><th class="center" colspan="4">'
            f'<img alt="{escape(course.detail_title)}" '
            'src="/data/teach/h12/img/202607221738_Cm29mRl.png"></th></tr>'
        ),
        1,
    )
    contract = andong._library_detail_contract(
        listed,
        BeautifulSoup(detail_html, "html.parser"),
    )
    row = contract["row"]
    assert row["status"] == "WAITING"
    assert row["reservation_available"] is True
    assert row["application_type"] == "ONLINE_WAITLIST_LOGIN_REQUIRED"
    assert "apply_status=2" in row["application_url"]


def test_library_known_author_lecture_is_in_education_scope() -> None:
    listed = {
        "large_category_idx": "23",
        "group_idx": "147",
        "category_idx": "0",
        "teach_idx": "834",
        "category": "기타",
    }
    assert andong._library_scope_reason(listed, "8월 썬킴 작가초대석") == ""
