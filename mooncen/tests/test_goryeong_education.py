from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from hashlib import sha1, sha256
import os
from threading import Lock
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from Crawler import municipal_goryeong as goryeong


TARGET = {
    "provider": goryeong.GORYEONG_PROVIDER,
    "url": goryeong.GORYEONG_CANONICAL_URL,
}
LEGACY_TARGET = {
    "provider": goryeong.GORYEONG_PROVIDER,
    "url": goryeong.GORYEONG_LEGACY_URL,
}

EXPECTED_INSTITUTIONS = (
    "평생학습관",
    "도시과",
    "보건소",
    "농업기술센터",
    "대가야읍",
    "덕곡면",
    "운수면",
    "성산면",
    "다산면",
    "개진면",
    "우곡면",
    "쌍림면",
    "고령문화원",
    "다산도서관",
    "고령도서관",
)
EXPECTED_TYPES = ("생활취미", "외국어", "정보화", "자격증", "기타")
EXPECTED_STATUSES = {"AI": "신청가능", "AA": "접수예정"}
PAGE_SIZE = 5


@dataclass(frozen=True)
class SyntheticCourse:
    ep_idx: str
    institution: str
    course_type: str
    status_filter: str
    source_status: str
    title: str
    target: str
    apply_start: str
    apply_end: str
    event_start: str
    event_end: str
    venue: str
    education_time: str
    fee: str
    materials: str
    capacity_current: int
    capacity_total: int


def _courses() -> list[SyntheticCourse]:
    institutions = (
        "평생학습관",
        "다산도서관",
        "도시과",
        "보건소",
        "농업기술센터",
        "대가야읍",
        "덕곡면",
    )
    course_types = (
        "생활취미",
        "외국어",
        "정보화",
        "자격증",
        "기타",
        "생활취미",
        "기타",
    )
    rows: list[SyntheticCourse] = []
    for offset in range(7):
        scheduled = offset == 6
        source_status = (
            "접수예정" if scheduled else "신청하기" if offset < 2 else "접수종료"
        )
        rows.append(
            SyntheticCourse(
                ep_idx=str(9700 + offset),
                institution=institutions[offset],
                course_type=course_types[offset],
                status_filter="AA" if scheduled else "AI",
                source_status=source_status,
                title=f"고령군 완전성 검증 강좌 {offset + 1}",
                target="고령군민" if offset != 1 else "관내 초등 1~4학년",
                apply_start="2026-07-24" if scheduled else "2026-07-01",
                apply_end="2026-08-01" if scheduled else "2026-07-31",
                event_start="2026-08-10" if scheduled else "2026-07-20",
                event_end="2026-09-10" if scheduled else "2026-11-25",
                venue=f"안전한 교육실 {offset + 1}",
                education_time="매주 수요일 14;00~16;00",
                fee="무료" if offset % 2 == 0 else "10,000원",
                materials="필기도구",
                capacity_current=offset,
                capacity_total=10 + offset,
            )
        )
    return rows


def _radios(
    name: str,
    values: tuple[tuple[str, str], ...],
    selected: str,
) -> str:
    institution_ids = {
        label: number
        for label, number in zip(
            EXPECTED_INSTITUTIONS,
            (1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 2, 15),
            strict=True,
        )
    }
    return "".join(
        f'<li><input type="radio" name="{name}" '
        f'id="{name}{institution_ids.get(value, index) if name == "searchInst" else index}" '
        f'value="{value}"{" checked" if selected and selected == value else ""}>'
        f'<label for="{name}{institution_ids.get(value, index) if name == "searchInst" else index}">{label}</label></li>'
        for index, (value, label) in enumerate(values, 1)
    )


def _form(
    *,
    requested_page: int,
    effective_page: int,
    filter_name: str,
    filter_value: str,
    registry_drift: bool = False,
    selection_drift: bool = False,
) -> str:
    institutions = EXPECTED_INSTITUTIONS[:-1] if registry_drift else EXPECTED_INSTITUTIONS
    selected_inst = filter_value if filter_name == "searchInst" else ""
    selected_type = filter_value if filter_name == "searchType" else ""
    selected_status = filter_value if filter_name == "searchStatus" else ""
    if selection_drift:
        selected_inst = selected_type = selected_status = ""
    return f"""
      <form id="frm" name="frm" method="post" action="?">
        <input type="hidden" id="page" name="pageIndex" value="">
        <input type="hidden" id="eduPage" name="eduPage" value="{"" if requested_page == 1 else requested_page}">
        <input type="hidden" id="kmoocPage" name="kmoocPage" value="">
        <input type="hidden" id="eduPageIndex" name="eduPageIndex" value="{effective_page}">
        <input type="hidden" id="kmoocPageIndex" name="kmoocPageIndex" value="1">
        <input type="hidden" id="IDX" name="IDX" value="35">
        <div class="selectList"><ul>
          {_radios("searchInst", tuple((item, item) for item in institutions), selected_inst)}
        </ul></div>
        <div class="selectList"><ul>
          {_radios("searchType", tuple((item, item) for item in (*EXPECTED_TYPES, "K-MOOC")), selected_type)}
        </ul></div>
        <div class="selectList"><ul>
          {_radios("searchStatus", (("", "전체검색"), *tuple(EXPECTED_STATUSES.items())), selected_status)}
        </ul></div>
        <input type="text" id="boardSearch" name="pageKeyword" value="">
        <button type="button" onclick="fn_search()">검색</button>
      </form>
    """


def _course_row(course: SyntheticCourse, number: int) -> str:
    if course.source_status == "신청하기":
        control = (
            f'<a class="btn apply" href="javascript:" '
            f'onclick="doApply(\'{course.ep_idx}\')">신청하기</a>'
        )
    elif course.source_status == "접수예정":
        control = '<span class="btn wait">접수예정</span>'
    else:
        control = '<span class="btn end">접수종료</span>'
    return f"""
      <tr>
        <td class="num moNone">{number}</td>
        <td class="field">{course.course_type}</td>
        <td class="place">{course.institution}</td>
        <td class="subject">
          <a href="/lifelong/eduProgram/detail.do?IDX=35&amp;epIdx={course.ep_idx}">
            {course.title}<span>(정원 {course.capacity_total}명 / 신청 {course.capacity_current}명)</span>
          </a>
        </td>
        <td class="target">{course.target}</td>
        <td class="period">
          <p class="t1"><span>접수기간</span> {course.apply_start}~{course.apply_end}</p>
          <p class="t2"><span>교육기간</span> {course.event_start}~{course.event_end}</p>
        </td>
        <td class="btnCell">{control}</td>
      </tr>
    """


def _internal_board(rows: list[SyntheticCourse], *, bad_empty: bool = False) -> str:
    body = "".join(_course_row(course, len(rows) - index) for index, course in enumerate(rows))
    empty = ""
    if not rows:
        message = "빈 결과 문구가 바뀌었습니다." if bad_empty else "등록된 교육강좌가 없습니다."
        empty = f'<div class="noData">{message}</div>'
    return f"""
      <div class="boardList">
        <table class="dataTable">
          <caption>수강신청 목록</caption>
          <thead><tr>
            <th class="num">번호</th><th class="field">분야</th>
            <th class="place">기관</th><th class="subject">교육강좌명 (정원/신청)</th>
            <th class="target">모집대상</th><th class="period">접수 및 교육기간</th>
            <th class="btnCell">수강신청</th>
          </tr></thead>
          <tbody><h3>오프라인 강좌</h3>{body}</tbody>
        </table>
        {empty}
      </div>
    """


def _page_nav(current: int, last: int, *, kmooc: bool = False) -> str:
    function = "fnKmoocLinkPage" if kmooc else "fnEduLinkPage"
    pages = "".join(
        f"<li><strong>{page}</strong></li>"
        if page == current
        else f'<li><a href="javascript:;" onclick="{function}({page})">{page}</a></li>'
        for page in range(1, min(last, 10) + 1)
    )
    last_link = (
        f'<li class="pageBtn last"><a href="javascript:;" '
        f'onclick="{function}({last})">끝</a></li>'
        if last > 1
        else ""
    )
    return f"""
      <div class="pageNav">
        <ul class="pcVer">{pages}{last_link}</ul>
        <ul class="mVer">{pages}{last_link}</ul>
      </div>
    """


def _kmooc_decoy() -> str:
    return """
      <div class="boardList">
        <table class="dataTable"><caption>수강신청 목록</caption><tbody>
          <h3>K-MOOC 강좌</h3>
          <tr>
            <td class="num">1,365</td><td class="field">K-MOOC</td>
            <td class="place">외부대학교</td>
            <td class="subject"><a href="/lifelong/eduProgram/detail2.do?IDX=35&amp;epAddr=https://example.invalid/private"></a>K-MOOC 제외 강좌</td>
            <td class="target">-</td>
            <td class="period"><p class="t1"><span>접수기간</span> 2026-01-01~2026-12-31</p><p class="t2"><span>교육기간</span> 2026-01-01~2026-12-31</p></td>
            <td class="btnCell"><a class="btn apply" href="https://example.invalid/apply">신청하기</a></td>
          </tr>
        </tbody></table>
      </div>
      <div class="pageNav"><ul class="pcVer">
        <li><strong>1</strong></li>
        <li><a href="javascript:;" onclick="fnKmoocLinkPage(2)">2</a></li>
        <li class="pageBtn last"><a href="javascript:;" onclick="fnKmoocLinkPage(273)">끝</a></li>
      </ul></div>
    """


def _list_html(
    rows: list[SyntheticCourse],
    *,
    requested: int,
    current: int,
    last: int,
    filter_name: str,
    filter_value: str,
    registry_drift: bool = False,
    selection_drift: bool = False,
    bad_empty: bool = False,
) -> str:
    return f"""
      <!doctype html><html lang="ko"><head><meta charset="utf-8">
        <title>고령군 평생교육포털 - 진행중강좌</title>
      </head><body>
        <h1 class="logo">고령군 평생교육포털</h1>
        <h3 class="pageTit">진행중강좌</h3><h3 class="pageTit">진행중강좌</h3>
        <main id="content">
          <h2>진행중강좌</h2>
          {_form(requested_page=requested, effective_page=current, filter_name=filter_name, filter_value=filter_value, registry_drift=registry_drift, selection_drift=selection_drift)}
          {_internal_board(rows, bad_empty=bad_empty)}
          {_page_nav(current, last)}
          {_kmooc_decoy()}
        </main>
        <footer class="footer"><strong>고령군평생학습관</strong>
          <p>40138) 경상북도 고령군 왕릉로 30 고령군평생학습관 TEL. 054-950-6363 FAX. 054-950-6349</p>
        </footer>
      </body></html>
    """


def _detail_html(
    course: SyntheticCourse,
    *,
    title_suffix: str = "",
    identity: str | None = None,
    capacity_delta: int = 0,
) -> str:
    bound_identity = identity or course.ep_idx
    return f"""
      <!doctype html><html lang="ko"><head><meta charset="utf-8">
        <title>고령군 평생교육포털 - 진행중강좌</title>
      </head><body>
        <h1 class="logo">고령군 평생교육포털</h1>
        <h3 class="pageTit">진행중강좌</h3><h3 class="pageTit">진행중강좌</h3>
        <div class="viewCourse">
          <div class="viewTop"><div class="txtArea">
            <h3 class="topTit">{course.title}{title_suffix}</h3>
            <ul>
              <li><span class="tit">기관</span><span class="txt">{course.institution}</span></li>
              <li><span class="tit">교육구분</span><span class="txt">{course.course_type}</span></li>
            </ul>
          </div></div>
          <table class="conTable"><tbody>
            <tr><td class="th">위치</td><td colspan="3">{course.venue}</td></tr>
            <tr><td class="th">접수기간</td><td>{course.apply_start} ~ {course.apply_end}</td><td class="th">교육기간</td><td>{course.event_start} ~ {course.event_end}</td></tr>
            <tr><td class="th">접수처</td><td>담당자 010-1234-5678</td><td class="th">교육시간</td><td>{course.education_time}</td></tr>
            <tr><td class="th">수강료</td><td>{course.fee}</td><td class="th">준비물</td><td>{course.materials}</td></tr>
            <tr><td class="th">모집대상</td><td>{course.target}</td><td class="th">모집방법</td><td>https://forms.example.test/private-application</td></tr>
            <tr><td class="th">모집인원</td><td>{course.capacity_total + capacity_delta}명</td><td class="th">신청인원</td><td>{course.capacity_current}명</td></tr>
            <tr><td class="th">교육과정</td><td colspan="3">저장하면 안 되는 자유 교육과정</td></tr>
            <tr><td class="th">수강안내</td><td colspan="3">private@example.test / 000은행 123-456-789 / 054-000-0000</td></tr>
            <tr><td class="th">첨부파일</td><td colspan="3"><a href="/front/downFile.do?fileId=PRIVATE">개인신청서.pdf</a></td></tr>
          </tbody></table>
          <h4 class="conTit">기타</h4>
          <table class="conTable"><tbody>
            <tr><td class="th">편의제공</td><td colspan="3">저장하지 않는 기타 자유본문</td></tr>
            <tr><td class="th">기타사항</td><td colspan="3">저장하지 않는 추가 자유본문</td></tr>
          </tbody></table>
          <div class="btnBox"><ul>
            <li><a href="/lifelong/eduProgram/list.do?IDX=35">목록</a></li>
            <li><a class="submitBtn" href="javascript:" onclick="doApply('{bound_identity}')">신청하기</a></li>
          </ul></div>
        </div>
        <footer class="footer"><strong>고령군평생학습관</strong>
          <p>40138) 경상북도 고령군 왕릉로 30 고령군평생학습관 TEL. 054-950-6363 FAX. 054-950-6349</p>
        </footer>
      </body></html>
    """


class FakeResponse:
    def __init__(self, url: str, html: str, status_code: int = 200):
        self.url = url
        self.content = html.encode("utf-8")
        self.text = html
        self.status_code = status_code
        self.history: list[object] = []
        self.headers = {"Content-Type": "text/html;charset=UTF-8"}
        self.encoding = "utf-8"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self) -> None:
        self.closed = False
        self.headers: dict[str, str] = {}

    def close(self) -> None:
        self.closed = True


class SyntheticBackend:
    def __init__(
        self,
        *,
        duplicate_identity: bool = False,
        nonempty_post_last: bool = False,
        unstable_page: int = 0,
        registry_drift: bool = False,
        selection_drift: bool = False,
        bad_empty: bool = False,
        missing_partition: bool = False,
        overlap_partition: bool = False,
        detail_title_mismatch: str = "",
        detail_identity_mismatch: str = "",
        detail_capacity_mismatch: str = "",
        response_url_drift: bool = False,
    ) -> None:
        self.courses = _courses()
        if duplicate_identity:
            self.courses[1] = replace(self.courses[1], ep_idx=self.courses[0].ep_idx)
        self.nonempty_post_last = nonempty_post_last
        self.unstable_page = unstable_page
        self.registry_drift = registry_drift
        self.selection_drift = selection_drift
        self.bad_empty = bad_empty
        self.missing_partition = missing_partition
        self.overlap_partition = overlap_partition
        self.detail_title_mismatch = detail_title_mismatch
        self.detail_identity_mismatch = detail_identity_mismatch
        self.detail_capacity_mismatch = detail_capacity_mismatch
        self.response_url_drift = response_url_drift
        self.urls: list[str] = []
        self.calls: Counter[tuple[str, str, int]] = Counter()
        self.lock = Lock()

    def _filtered(self, name: str, value: str) -> list[SyntheticCourse]:
        if not name:
            return list(self.courses)
        if name == "searchInst":
            return [row for row in self.courses if row.institution == value]
        if name == "searchType":
            rows = [row for row in self.courses if row.course_type == value]
            if self.missing_partition and value == "기타":
                return rows[:-1]
            return rows
        if name == "searchStatus":
            rows = [row for row in self.courses if row.status_filter == value]
            if self.overlap_partition and value == "AA":
                rows.append(self.courses[0])
            return rows
        raise AssertionError(f"unexpected list filter: {name}={value}")

    def _list_response(self, url: str, query: Mapping[str, list[str]]) -> FakeResponse:
        assert query.get("IDX", [""])[0] == "35"
        filters = [
            (name, query.get(name, [""])[0])
            for name in ("searchInst", "searchType", "searchStatus")
            if query.get(name, [""])[0]
        ]
        assert len(filters) <= 1
        filter_name, filter_value = filters[0] if filters else ("", "")
        page = int(query.get("eduPageIndex", ["1"])[0] or "1")
        assert page >= 1
        with self.lock:
            self.calls[(filter_name, filter_value, page)] += 1
            call_number = self.calls[(filter_name, filter_value, page)]
        all_rows = self._filtered(filter_name, filter_value)
        last = max(1, (len(all_rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        rows = all_rows[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
        if self.nonempty_post_last and not filter_name and page == last + 1:
            rows = [self.courses[-1]]
        if not filter_name and page == self.unstable_page and call_number >= 2 and rows:
            rows[0] = replace(rows[0], title=rows[0].title + " 변경")
        effective = min(page, last)
        html = _list_html(
            rows,
            requested=page,
            current=effective,
            last=last,
            filter_name=filter_name,
            filter_value=filter_value,
            registry_drift=self.registry_drift,
            selection_drift=(self.selection_drift and bool(filter_name)),
            bad_empty=(self.bad_empty and filter_name == "searchInst" and not rows),
        )
        return FakeResponse(url, html)

    def response(self, url: str) -> FakeResponse:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        with self.lock:
            self.urls.append(url)
        if parsed.path == "/lifelong/eduProgram/list.do":
            response = self._list_response(url, query)
        elif parsed.path == "/lifelong/eduProgram/detail.do":
            identity = query.get("epIdx", [""])[0]
            assert query.get("IDX", [""])[0] == "35"
            course = next(item for item in self.courses if item.ep_idx == identity)
            response = FakeResponse(
                url,
                _detail_html(
                    course,
                    title_suffix=" 불일치" if identity == self.detail_title_mismatch else "",
                    identity=("999999" if identity == self.detail_identity_mismatch else identity),
                    capacity_delta=1 if identity == self.detail_capacity_mismatch else 0,
                ),
            )
        else:
            raise AssertionError(f"collector called forbidden/unexpected endpoint: {url}")
        if self.response_url_drift:
            response.url = response.url.replace("www.goryeong.go.kr", "goryeong.go.kr")
        return response


def _fetch(backend: SyntheticBackend):
    def fetcher(_session: object, url: str, _timeout: int) -> FakeResponse:
        return backend.response(url)

    return fetcher


def _collect(backend: SyntheticBackend, **options: Any):
    return goryeong.collect_goryeong_education(
        TARGET,
        today="2026-07-23",
        session_factory=FakeSession,
        fetcher=_fetch(backend),
        **options,
    )


def _assert_atomic_failure(meta: Mapping[str, Any]) -> None:
    assert meta["configured_collection_error"]
    assert not meta["snapshot_complete"]
    assert not meta["full_snapshot_validated"]


def _status_registry() -> dict[str, str]:
    raw = goryeong.GORYEONG_STATUS_FILTERS
    if isinstance(raw, Mapping):
        return {str(key): str(value) for key, value in raw.items()}
    result: dict[str, str] = {}
    for item in raw:
        if isinstance(item, str):
            result[item] = EXPECTED_STATUSES[item]
        elif isinstance(item, tuple):
            result[str(item[0])] = str(item[1])
        else:
            result[str(item.code)] = str(item.label)
    return result


def test_exact_target_registry_hashes_and_legacy_retarget() -> None:
    assert goryeong.GORYEONG_PROVIDER == "MUNI_WWW_GORYEONG_GO_KR_8F708B74"
    assert goryeong.GORYEONG_CANONICAL_URL == (
        "https://www.goryeong.go.kr/lifelong/eduProgram/list.do?IDX=35"
    )
    assert goryeong.GORYEONG_LEGACY_URL == (
        "https://www.goryeong.go.kr/lifelong/eduProgram/detail.do?IDX=35&epIdx=75"
    )
    assert sha1(goryeong.GORYEONG_LEGACY_URL.encode()).hexdigest()[:8].upper() == "8F708B74"
    assert sha256(goryeong.GORYEONG_CANONICAL_URL.encode()).hexdigest() == (
        "458a8d7f1d64249d5a6c9f444470c5d437176b99fd795d97ba51d67f431b137a"
    )
    assert tuple(goryeong.GORYEONG_INSTITUTIONS) == EXPECTED_INSTITUTIONS
    assert tuple(goryeong.GORYEONG_INTERNAL_TYPES) == EXPECTED_TYPES
    assert _status_registry() == EXPECTED_STATUSES
    assert goryeong.is_goryeong_education_target(TARGET)
    assert goryeong.is_goryeong_education_target(LEGACY_TARGET)
    assert not goryeong.is_goryeong_education_target({**TARGET, "provider": "wrong"})
    assert not goryeong.is_goryeong_education_target(
        {**TARGET, "url": goryeong.GORYEONG_CANONICAL_URL + "&searchType=기타"}
    )
    assert not goryeong.is_goryeong_education_target(
        {
            **TARGET,
            "url": "https://www.goryeong.go.kr/lifelong/boardView.do?BOARD_IDX=133&IDX=18",
        }
    )
    assert not goryeong.is_goryeong_education_target(
        {
            **TARGET,
            "url": "https://www.goryeong.go.kr/lifelong/eduProgram/detail.do?IDX=4&epIdx=561",
        }
    )
    assert not goryeong.is_goryeong_education_target(
        {**TARGET, "url": goryeong.GORYEONG_CANONICAL_URL.replace("https://", "http://")}
    )


def test_complete_paginated_partitions_details_privacy_and_kmooc_exclusion() -> None:
    backend = SyntheticBackend()
    rows, parser, meta = _collect(backend)
    assert parser == goryeong.GORYEONG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_rows"] == meta["source_identity_count"] == 7
    assert meta["current_source_count"] == meta["returned_count"] == len(rows) == 7
    assert meta["list_requests"] == 43
    assert meta["post_last_requests"] == 16
    assert meta["detail_pages"] == 7
    assert meta["source_requests"] == meta["request_attempts"] == 50
    assert meta["institution_filter_counts"] == {
        institution: 1 if institution in {item.institution for item in _courses()} else 0
        for institution in EXPECTED_INSTITUTIONS
    }
    assert meta["type_filter_counts"] == {
        "생활취미": 2,
        "외국어": 1,
        "정보화": 1,
        "자격증": 1,
        "기타": 2,
    }
    assert meta["status_filter_counts"] == {"AI": 6, "AA": 1}
    assert meta["institution_partition_union_count"] == 7
    assert meta["type_partition_union_count"] == 7
    assert meta["status_partition_union_count"] == 7
    assert meta["partition_overlap_count"] == 0
    assert meta["institution_partition_complete"]
    assert meta["type_partition_complete"]
    assert meta["status_partition_complete"]
    assert meta["pagination_complete"]
    assert meta["details_complete"]
    assert meta["full_ledger_rechecked_after_details"]
    assert meta["snapshot_complete"]
    assert meta["full_snapshot_validated"]
    assert Counter(row["status"] for row in rows) == {
        "OPEN": 2,
        "CLOSED": 4,
        "SCHEDULED": 1,
    }
    assert meta["application_control_count"] == 2
    assert meta["actionable_application_count"] == 2
    assert {row["raw_fields"]["identity"] for row in rows} == {
        item.ep_idx for item in _courses()
    }
    assert all(row["provider"] == goryeong.GORYEONG_PROVIDER for row in rows)
    assert all(row["description"] == row["title"] for row in rows)
    assert all(
        urlparse(row["branch_url"]).hostname == "www.goryeong.go.kr" for row in rows
    )
    assert all(
        bool(row["application_url"]) == row["reservation_available"] for row in rows
    )
    first = next(row for row in rows if row["raw_fields"]["identity"] == "9700")
    assert first["room"] == first["venue_name"] == "안전한 교육실 1"
    assert first["branch"] == "고령군평생학습관"
    assert first["target"] == "고령군민"
    assert first["fee"] == "무료"
    assert first["capacity_current"] == 0
    assert first["capacity_total"] == 10
    assert first["apply_start_date"] == "2026-07-01"
    assert first["apply_end_date"] == "2026-07-31"
    assert first["start_date"] == "2026-07-20"
    assert first["end_date"] == "2026-11-25"
    assert "14:00~16:00" in first["schedule_raw"]
    payload = repr(rows)
    for forbidden in (
        "K-MOOC 제외 강좌",
        "example.invalid",
        "010-1234-5678",
        "forms.example.test",
        "private@example.test",
        "000은행",
        "054-000-0000",
        "054-950-6363",
        "저장하면 안 되는 자유 교육과정",
        "개인신청서.pdf",
        "/front/downFile.do",
        "저장하지 않는 기타 자유본문",
    ):
        assert forbidden not in payload
    requested_paths = Counter(urlparse(url).path for url in backend.urls)
    assert requested_paths == {
        "/lifelong/eduProgram/list.do": 43,
        "/lifelong/eduProgram/detail.do": 7,
    }
    assert meta["application_endpoints_called"] == 0
    assert meta["attachment_endpoints_called"] == 0
    assert meta["pii_endpoints_called"] == 0
    assert meta["privacy_violations"] == 0


@pytest.mark.parametrize("unstable_page", (1, 2))
def test_first_or_last_page_stability_drift_fails_atomically(unstable_page: int) -> None:
    rows, _, meta = _collect(SyntheticBackend(unstable_page=unstable_page))
    assert rows == []
    _assert_atomic_failure(meta)


def test_nonempty_post_last_page_fails_before_details() -> None:
    backend = SyntheticBackend(nonempty_post_last=True)
    rows, _, meta = _collect(backend)
    assert rows == []
    _assert_atomic_failure(meta)
    assert not any(urlparse(url).path.endswith("/detail.do") for url in backend.urls)


@pytest.mark.parametrize(
    "options",
    (
        {"registry_drift": True},
        {"selection_drift": True},
        {"bad_empty": True},
        {"missing_partition": True},
        {"overlap_partition": True},
    ),
)
def test_filter_registry_selection_sentinel_and_partition_drift_fail_closed(
    options: Mapping[str, bool],
) -> None:
    rows, _, meta = _collect(SyntheticBackend(**options))
    assert rows == []
    _assert_atomic_failure(meta)


@pytest.mark.parametrize(
    "options",
    (
        {"detail_title_mismatch": "9700"},
        {"detail_identity_mismatch": "9700"},
        {"detail_capacity_mismatch": "9700"},
    ),
)
def test_detail_identity_title_and_capacity_binding_drift_is_atomic(
    options: Mapping[str, str],
) -> None:
    rows, _, meta = _collect(SyntheticBackend(**options))
    assert rows == []
    _assert_atomic_failure(meta)


def test_duplicate_list_identity_and_response_url_drift_are_rejected() -> None:
    for backend in (
        SyntheticBackend(duplicate_identity=True),
        SyntheticBackend(response_url_drift=True),
    ):
        rows, _, meta = _collect(backend)
        assert rows == []
        _assert_atomic_failure(meta)


def test_managed_session_limits_and_dedupe_cardinality_fail_closed() -> None:
    rows, _, meta = goryeong.collect_goryeong_education(TARGET, today="2026-07-23")
    assert rows == []
    assert "session" in meta["configured_collection_error"]
    assert meta["source_requests"] == 0

    rows, _, meta = _collect(SyntheticBackend(), max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"]
    _assert_atomic_failure(meta)

    backend = SyntheticBackend()
    rows, _, meta = _collect(backend, detail_limit=6)
    assert rows == []
    assert meta["source_cap_reached"]
    assert meta["detail_pages"] == 0
    assert not any(urlparse(url).path.endswith("/detail.do") for url in backend.urls)
    _assert_atomic_failure(meta)

    rows, _, meta = _collect(
        SyntheticBackend(),
        dedupe_rows=lambda values: values[:-1],
    )
    assert rows == []
    _assert_atomic_failure(meta)


class RecordingSession:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.headers = self.session.headers
        self.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
                "Accept": "text/html,application/xhtml+xml",
            }
        )
        self.urls: list[str] = []

    def get(self, url: str, **kwargs: Any):
        self.urls.append(url)
        return self.session.get(url, **kwargs)

    def close(self) -> None:
        self.session.close()


def _live_snapshot():
    tracker = RecordingSession()
    rows, parser, meta = goryeong.collect_goryeong_education(
        TARGET,
        today="2026-07-23",
        timeout=30,
        max_pages=5,
        detail_limit=20,
        session_factory=lambda: tracker,
    )
    return rows, parser, meta, tracker.urls


@pytest.mark.skipif(
    os.getenv("RUN_GORYEONG_LIVE") != "1",
    reason="set RUN_GORYEONG_LIVE=1 for two bounded official-source snapshots",
)
def test_live_official_source_twice_is_exact_complete_and_stable() -> None:
    snapshots = [_live_snapshot(), _live_snapshot()]
    first_rows, first_parser, first_meta, first_urls = snapshots[0]
    second_rows, second_parser, second_meta, second_urls = snapshots[1]
    assert first_parser == second_parser == goryeong.GORYEONG_PARSER
    assert first_rows == second_rows
    stable_keys = (
        "source_rows",
        "source_ep_indices",
        "source_status_counts",
        "source_type_counts",
        "institution_filter_counts",
        "type_filter_counts",
        "status_filter_counts",
        "status_counts",
        "returned_count",
    )
    assert {key: first_meta[key] for key in stable_keys} == {
        key: second_meta[key] for key in stable_keys
    }
    for rows, _, meta, urls in snapshots:
        assert meta["configured_collection_error"] == ""
        assert meta["source_requests"] == meta["request_attempts"] == 34
        assert meta["list_requests"] == 31
        assert meta["post_last_requests"] == 7
        assert meta["detail_pages"] == 3
        assert meta["source_rows"] == meta["source_identity_count"] == 3
        assert meta["current_source_count"] == meta["returned_count"] == len(rows) == 3
        assert set(meta["source_ep_indices"]) == {"718", "719", "720"}
        assert meta["source_status_counts"] == {"신청하기": 1, "접수종료": 2}
        assert meta["source_type_counts"] == {"생활취미": 2, "기타": 1}
        assert meta["institution_filter_counts"] == {
            institution: {"평생학습관": 1, "다산도서관": 2}.get(institution, 0)
            for institution in EXPECTED_INSTITUTIONS
        }
        assert meta["type_filter_counts"] == {
            "생활취미": 2,
            "외국어": 0,
            "정보화": 0,
            "자격증": 0,
            "기타": 1,
        }
        assert meta["status_filter_counts"] == {"AI": 3, "AA": 0}
        assert meta["status_counts"] == {"OPEN": 1, "CLOSED": 2}
        assert meta["application_control_count"] == 1
        assert meta["application_endpoints_called"] == 0
        assert meta["attachment_endpoints_called"] == 0
        assert meta["pii_endpoints_called"] == 0
        assert meta["privacy_violations"] == 0
        assert meta["pagination_complete"]
        assert meta["institution_partition_complete"]
        assert meta["type_partition_complete"]
        assert meta["status_partition_complete"]
        assert meta["details_complete"]
        assert meta["full_ledger_rechecked_after_details"]
        assert meta["snapshot_complete"]
        assert meta["full_snapshot_validated"]
        assert Counter(urlparse(url).path for url in urls) == {
            "/lifelong/eduProgram/list.do": 31,
            "/lifelong/eduProgram/detail.do": 3,
        }
    expected = {
        "718": ("다산도서관", "다산도서관", "기타", "CLOSED", 0, 0),
        "719": ("평생학습관", "고령군평생학습관", "생활취미", "OPEN", 0, 10),
        "720": ("다산도서관", "다산도서관", "생활취미", "CLOSED", 0, 0),
    }
    for row in first_rows:
        identity = row["raw_fields"]["identity"]
        assert (
            row["raw_fields"]["source_institution"],
            row["branch"],
            row["category"],
            row["status"],
            row["capacity_current"],
            row["capacity_total"],
        ) == expected[identity]
        assert bool(row["application_url"]) == row["reservation_available"]
    assert first_meta["source_requests"] + second_meta["source_requests"] == 68
    assert len(first_urls) + len(second_urls) == 68
