from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
import ssl
import threading
from typing import Any, Mapping
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from Crawler import municipal_gangwon_goseong as goseong


TARGET = {
    "provider": goseong.GANGWON_GOSEONG_PROVIDER,
    "url": goseong.GANGWON_GOSEONG_CANONICAL_URL,
    "candidate_id": goseong.GANGWON_GOSEONG_CANDIDATE_ID,
}


@dataclass(frozen=True)
class Course:
    source: str
    identity: str
    title: str
    status: str
    start: str
    end: str
    apply_start: str
    apply_end: str
    target: str
    venue: str
    department: str = ""
    schedule: str = "월 10:00~12:00"
    current: int = 3
    capacity: int = 10
    waiting_current: int = 1
    waiting_total: int = 2
    fee: str = "0"


class DummySession:
    def close(self) -> None:
        return None


class Response:
    def __init__(
        self,
        html: str,
        url: str,
        *,
        content_type: str = "text/html; charset=UTF-8",
        history: tuple[Any, ...] = (),
    ) -> None:
        self.status_code = 200
        self.content = html.encode("utf-8")
        self.url = url
        self.headers = {"Content-Type": content_type}
        self.history = history


def _courses() -> dict[str, list[Course]]:
    result: dict[str, list[Course]] = {}
    counts = {"general": 11, "information": 2, "youth": 11, "resident": 11}
    current_status = {
        "general": "접수중",
        "information": "모집마감",
        "youth": "모집중",
        "resident": "대기중",
    }
    expired_status = {
        "general": "접수마감",
        "information": "모집마감",
        "youth": "모집마감",
        "resident": "모집마감",
    }
    venues = {
        "general": "",
        "information": "고성군청 정보화교육장",
        "youth": "토성청소년문화의집 밴드연습실",
        "resident": "현내면행정복지센터 민방위대피소",
    }
    targets = {
        "general": "고성군민",
        "information": "고성군민",
        "youth": "초등4~중등3",
        "resident": "현내면 주민 우선 고성군민",
    }
    for source, count in counts.items():
        rows = []
        for index in range(count):
            is_current = index == 0
            rows.append(
                Course(
                    source=source,
                    identity=str(100 + index),
                    title=f"{source} 강좌 {index + 1}",
                    status=(
                        current_status[source]
                        if is_current
                        else expired_status[source]
                    ),
                    start="2099-08-01" if is_current else "2020-01-01",
                    end="2099-08-31" if is_current else "2020-01-31",
                    apply_start="2099-07-01" if is_current else "2019-12-01",
                    apply_end="2099-07-31" if is_current else "2019-12-20",
                    target=targets[source],
                    venue=venues[source],
                    department=("교육행정팀" if source == "general" else ""),
                    fee=("무료" if source == "general" else "0"),
                )
            )
        result[source] = rows
    return result


def _general_form(source: goseong.GoseongSource) -> str:
    return f"""
      <form id="searchForm" name="searchForm" method="post"
            action="{source.list_path}">
        <input name="pageIndex" value="1"><input name="eduNo" value="">
        <select name="searchDeptcode"><option value="">전체</option></select>
        <input name="searchRcptBgngDt" value="">
        <input name="searchRcptEndDt" value="">
        <input name="searchEduBgngYmd" value="">
        <input name="searchEduEndYmd" value="">
        <input name="searchKeyword" value="">
      </form>
    """


def _table_form(source: goseong.GoseongSource) -> str:
    kind = (
        '<select name="kind"><option value="">-학기 전체-</option></select>'
        if source.code in {"youth", "resident"}
        else ""
    )
    return f"""
      <form name="eduSearchForm" method="post" action="{source.list_path}">
        <input name="pageUnit" value="10"><input name="pageIndex" value="1">
        <input name="pageSize" value="10"><input name="integrDeptCode" value="">
        <input name="searchCtgry" value="">{kind}
        <select name="groupYn"><option value="">기준</option></select>
        <select name="state"><option value="">상태</option></select>
        <input name="stDt" value=""><input name="edDt" value="">
        <input name="searchCondition" value="subject">
        <input name="searchKeyword" value="">
      </form>
    """


def _general_card(course: Course) -> str:
    capacity = (
        f"{course.current} / {course.capacity} 명 "
        f"(대기 {course.waiting_current} / {course.waiting_total} 명)"
    )
    fields = (
        ("부서명", course.department),
        ("접수기간", f"{course.apply_start} 09:00 ~ {course.apply_end} 18:00"),
        ("교육기간", f"{course.start} ~ {course.end}"),
        ("신청/모집인원", capacity),
        ("교육시간", course.schedule),
    )
    items = "".join(
        f'<li><span class="tit">{escape(label)}</span>'
        f'<span class="txt">{escape(value)}</span></li>'
        for label, value in fields
    )
    return f"""
      <div class="item" data-key-no="{course.identity}">
        <button class="button_detail"><div class="status-wrap">
          <span class="type">선착순</span><span class="status">{course.status}</span>
        </div><strong class="title">{escape(course.title)}</strong>
        <ul class="list-1st">{items}</ul></button>
      </div>
    """


_HEADERS = (
    "강좌명/강사명",
    "대상",
    "접수기간",
    "교육기간",
    "신청인원/모집인원",
    "시간",
    "상태",
)


def _table_row(source: goseong.GoseongSource, course: Course, page: int) -> str:
    href = source.detail_path + "?" + urlencode(
        {"pageIndex": page, "eduNo": course.identity}
    )
    return f"""
      <tr>
        <td data-cell-header="강좌명/강사명"><a href="{escape(href)}">
          {escape(course.title)}</a><br>(강사: 김강사 010-1111-2222)</td>
        <td data-cell-header="대상">{escape(course.target)}</td>
        <td data-cell-header="접수기간">{course.apply_start} 09:00<br>
          {course.apply_end} 18:00</td>
        <td data-cell-header="교육기간">{course.start}<br>{course.end}</td>
        <td data-cell-header="신청인원/모집인원">
          {course.current} / {course.capacity} 대기({course.waiting_current}/{course.waiting_total})
        </td>
        <td data-cell-header="시간">{escape(course.schedule)}</td>
        <td data-cell-header="상태"><a href="{escape(href)}">
          <span>{course.status}</span></a></td>
      </tr>
    """


def _application_href(source: str, identity: str) -> str:
    prefixes = {
        "general": "/prog/eduDscsnAply/yeyak/sub01_01/write.do",
        "information": "/prog/infoedu_reserve/info/sub03_060101/write.do",
        "youth": "/prog/lecReserve/youth/sub03_060401/write.do",
        "resident": "/prog/lecReserve/EMD/sub03_060501/write.do",
    }
    return prefixes[source] + "?" + urlencode({"eduNo": identity})


def _detail_html(
    source: goseong.GoseongSource,
    course: Course,
    *,
    title_mismatch: bool = False,
    application_identity_mismatch: bool = False,
    missing_active_control: bool = False,
    active_on_closed: bool = False,
    unknown_field: bool = False,
) -> str:
    title = course.title + (" 변경" if title_mismatch else "")
    unknown = '<div class="li"><b>비밀필드</b>변경</div>' if unknown_field else ""
    if source.layout == "cards":
        fields = f"""
          <div class="li"><b>교육기간</b>{course.start} ~ {course.end}</div>
          <div class="li"><b>교육시간</b>{escape(course.schedule)}</div>
          <div class="li"><b>접수기간</b>{course.apply_start} 09:00 ~ {course.apply_end} 18:00</div>
          <div class="li"><b>교육대상</b>{escape(course.target)}</div>
          <div class="li"><b>강사명</b>김강사 010-1111-2222</div>
          <div class="li"><b>수강료</b>{escape(course.fee)}</div>
          <div class="li"><b>교재비</b>교재 별도</div>
          <div class="li"><b>문의처</b>담당자 033-680-1234 staff@example.org</div>
          <div class="li"><b>신청/모집인원</b>
            {course.current} / {course.capacity} 명
            (대기 {course.waiting_current} / {course.waiting_total} 명)
          </div>
          <div class="li"><b>첨부파일</b>신청서.hwp</div>{unknown}
        """
        active = course.status == "접수중" or active_on_closed
        if active and not missing_active_control:
            application_identity = (
                str(int(course.identity) + 1)
                if application_identity_mismatch
                else course.identity
            )
            control = (
                '<button type="button" class="fe-btn fe-btn-primary button_aply">'
                "신청하기</button>"
            )
            application_script = f"""
              <form id="actionForm" name="actionForm"
                    action="{goseong.GANGWON_GOSEONG_GENERAL_APPLICATION_PATH}"
                    method="post">
                <input type="hidden" name="eduNo"
                       value="{escape(application_identity)}">
              </form>
              <script>
                $(".button_aply").click(function() {{
                  $("#actionForm").submit();
                }});
              </script>
            """
        else:
            control = ""
            application_script = ""
        summary = ""
        control_area = (
            '<div class="fe-btn_box"><div class="box-footer-inner">'
            f'{control}<button class="button_list">목록</button></div></div>'
            f"{application_script}"
        )
    else:
        fields = f"""
          <div class="li"><b>교육시간</b>{escape(course.schedule)}</div>
          <div class="li"><b>교육기간</b>{course.start} ~ {course.end}</div>
          <div class="li"><b>접수기간</b>{course.apply_start} 09:00 ~ {course.apply_end} 18:00</div>
          <div class="li"><b>담당자</b>김담당 010-1111-2222 staff@example.org</div>
          <div class="li"><b>수업료</b>{escape(course.fee)}</div>
          <div class="li"><b>교재비</b>0</div>
          <div class="li"><b>재료비</b>재료 별도</div>{unknown}
        """
        summary = f"""
          <div class="apply-article">
            <div class="item"><strong>교육정원</strong><em>{course.capacity} 명</em></div>
            <div class="item"><strong>교육대상</strong><em>{escape(course.target)}</em></div>
            <div class="item"><strong>교육장소</strong><em>{escape(course.venue)}</em></div>
            <div class="item"><strong>문의전화</strong><em>033-680-9999</em></div>
          </div>
        """
        is_open = course.status in {"모집중", "대기자모집중"} or active_on_closed
        if is_open and not missing_active_control:
            identity = str(int(course.identity) + 1) if application_identity_mismatch else course.identity
            control = (
                f'<a href="{escape(_application_href(source.code, identity))}">신청하기</a>'
            )
        else:
            label = "접수대기" if course.status == "대기중" else "접수마감"
            control = (
                f'<a href="#" onclick="javascript:alert(\'{label}\');">{label}</a>'
            )
        control_area = f'<div class="figure"><div class="btn_wrap">{control}</div></div>'
    return f"""
      <html><body><h2 class="page__title">{source.heading}</h2>
        <nav><a href="/prog/lecReserve/{source.code}/confirm/list.do">신청확인</a></nav>
        <strong class="caption-title">{escape(title)}</strong>
        <div class="caption-info">{fields}</div>{summary}{control_area}
        <aside>신청자 홍길동 010-9999-8888 applicant@example.org 상세설명</aside>
      </body></html>
    """


class FakeSite:
    def __init__(self, *, no_current: bool = False) -> None:
        self.courses = _courses()
        if no_current:
            for source, rows in self.courses.items():
                closed = "접수마감" if source == "general" else "모집마감"
                self.courses[source] = [
                    replace(
                        row,
                        status=closed,
                        start="2020-01-01",
                        end="2020-01-31",
                        apply_start="2019-12-01",
                        apply_end="2019-12-20",
                    )
                    for row in rows
                ]
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.page_one_calls: dict[str, int] = {}
        self.lock = threading.Lock()
        self.page_one_mutation = False
        self.sentinel_mutation = False
        self.duplicate_identity = False
        self.detail_title_mismatch = False
        self.application_identity_mismatch = False
        self.missing_active_control = False
        self.active_on_closed = False
        self.unknown_detail_field = False
        self.header_mutation = False
        self.bad_content_type = False
        self.redirected = False

    def _source(self, path: str) -> goseong.GoseongSource:
        for source in goseong.GANGWON_GOSEONG_SOURCES:
            if path in {source.list_path, source.detail_path}:
                return source
        raise AssertionError(f"unexpected path {path}")

    def _list_html(
        self, source: goseong.GoseongSource, page: int, page_one_call: int
    ) -> str:
        all_rows = self.courses[source.code]
        last = max(1, (len(all_rows) + 9) // 10)
        start = (page - 1) * 10
        rows = list(all_rows[start : start + 10]) if page <= last else []
        if self.sentinel_mutation and page == last + 1:
            rows = [all_rows[-1]]
        if self.duplicate_identity and page == 2 and rows:
            rows[0] = replace(rows[0], identity=all_rows[0].identity)
        if self.page_one_mutation and page == 1 and page_one_call > 1 and rows:
            rows[0] = replace(rows[0], title=rows[0].title + " 변경")
        if source.layout == "cards":
            body = "".join(_general_card(row) for row in rows)
            empty = "<p>데이터가 없습니다.</p>" if not rows else ""
            form = _general_form(source)
            content = f'<div class="list-wrap">{body}</div>{empty}'
        else:
            headers = list(_HEADERS)
            if self.header_mutation:
                headers[-1] = "상태변경"
            heading = "".join(f"<th>{escape(value)}</th>" for value in headers)
            body = "".join(_table_row(source, row, page) for row in rows)
            form = _table_form(source)
            content = f"<table><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table>"
        return f"""
          <html><body><h2 class="page__title">{source.heading}</h2>{form}
            <div class="total">총 게시물 {len(all_rows)} 개</div>{content}
          </body></html>
        """

    def fetcher(
        self,
        session: Any,
        method: str,
        url: str,
        data: Mapping[str, str] | None,
        timeout: int,
    ) -> Response:
        del session, timeout
        parsed = urlparse(url)
        source = self._source(parsed.path)
        payload = dict(data or {})
        with self.lock:
            self.calls.append((method, url, payload))
        if parsed.path == source.list_path:
            page = int(
                payload.get("pageIndex")
                if source.layout == "cards"
                else parse_qs(parsed.query).get("pageIndex", ["1"])[0]
            )
            with self.lock:
                if page == 1:
                    self.page_one_calls[source.code] = (
                        self.page_one_calls.get(source.code, 0) + 1
                    )
                count = self.page_one_calls.get(source.code, 0)
            html = self._list_html(source, page, count)
        else:
            identity = (
                payload.get("eduNo", "")
                if source.layout == "cards"
                else parse_qs(parsed.query).get("eduNo", [""])[0]
            )
            course = next(row for row in self.courses[source.code] if row.identity == identity)
            first_current = self.courses[source.code][0].identity == identity
            html = _detail_html(
                source,
                course,
                title_mismatch=self.detail_title_mismatch and first_current,
                application_identity_mismatch=(
                    self.application_identity_mismatch and first_current
                ),
                missing_active_control=self.missing_active_control and first_current,
                active_on_closed=(
                    self.active_on_closed
                    and source.code == "information"
                    and first_current
                ),
                unknown_field=self.unknown_detail_field and first_current,
            )
        return Response(
            html,
            url,
            content_type=("application/json" if self.bad_content_type else "text/html"),
            history=((object(),) if self.redirected else ()),
        )


def _collect(site: FakeSite, **kwargs: Any):
    crawl_today = kwargs.pop("today", "2026-07-21")
    return goseong.collect_gangwon_goseong_education(
        TARGET,
        today=crawl_today,
        session_factory=DummySession,
        fetcher=site.fetcher,
        **kwargs,
    )


def test_target_alias_and_owner_boundaries_are_exact() -> None:
    assert goseong.is_gangwon_goseong_education_target(TARGET)
    assert not goseong.is_gangwon_goseong_education_target(
        {**TARGET, "provider": "MUNI_WWW_GOSEONG_GO_KR_OTHER_REGION"}
    )
    for unsafe in (
        "http://www.gwgs.go.kr/",
        "https://www.gwgs.go.kr/?page=1",
        "https://user@www.gwgs.go.kr/",
        "https://www.gwgs.go.kr:443/",
        "https://www.gwgs.go.kr/#fragment",
    ):
        assert not goseong.is_gangwon_goseong_education_target(
            {**TARGET, "url": unsafe}
        )
    assert all(
        goseong.is_gangwon_goseong_alias_target({"url": url})
        for url in goseong.GANGWON_GOSEONG_ALIAS_URLS
    )
    assert all(
        goseong.is_gangwon_goseong_excluded_target({"url": url})
        for url in (
            *goseong.GANGWON_GOSEONG_EXCLUDED_URLS.values(),
            *goseong.GANGWON_GOSEONG_SEPARATE_OWNER_URLS.values(),
        )
    )


def test_complete_snapshot_paginates_details_branches_and_never_fetches_application() -> None:
    site = FakeSite()
    rows, parser, meta = _collect(site)

    assert parser == goseong.GANGWON_GOSEONG_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["source_total"] == 35
    assert meta["source_totals"] == {
        "general": 11,
        "information": 2,
        "youth": 11,
        "resident": 11,
    }
    assert meta["required_list_requests"] == 15
    assert meta["list_requests"] == 15
    assert meta["sentinel_requests"] == 4
    assert meta["stability_rechecks"] == 4
    assert meta["detail_attempts"] == 4
    assert meta["detail_pages"] == 4
    assert meta["raw_cross_source_identity_collision_count"] > 0
    assert len(rows) == 4
    assert {row["provider_course_id"] for row in rows} == {
        "general:100",
        "information:100",
        "youth:100",
        "resident:100",
    }
    assert {row["branch"] for row in rows} == {
        "고성군청 교육문화과 교육행정팀",
        "고성군청 정보화교육장",
        "토성청소년문화의집",
        "현내면 주민자치센터",
    }
    assert meta["public_application_control_count"] == 2
    open_rows = [row for row in rows if row["status"] == "OPEN"]
    assert len(open_rows) == 2
    assert all(row["reservation_available"] for row in open_rows)
    by_source = {row["source_group"]: row for row in open_rows}
    assert by_source["general"]["application_url"] == by_source["general"]["raw_url"]
    assert (
        by_source["general"]["raw_fields"]["application_control_contract"]
        == "identity_bound_post_control"
    )
    assert "eduNo=100" in by_source["youth"]["application_url"]
    assert by_source["general"]["venue_name"] == "장소 별도 안내"
    assert (
        by_source["general"]["raw_fields"]["venue_evidence"]
        == "official_detail_omits_venue"
    )
    assert all(
        all(
            row.get(field)
            for field in (
                "target",
                "fee",
                "start_date",
                "end_date",
                "venue_name",
                "category",
                "schedule_raw",
            )
        )
        for row in rows
    )
    assert all(row["raw_fields"]["detail_verified"] for row in rows)
    assert all(
        row["raw_fields"]["application_control_verified"] for row in rows
    )

    fetched_urls = [url for _, url, _ in site.calls]
    assert not any("eduDscsnAply" in url for url in fetched_urls)
    assert not any("infoedu_reserve" in url for url in fetched_urls)
    assert not any("lecReserve" in url for url in fetched_urls)
    assert not any("confirm" in url for url in fetched_urls)
    persisted = repr(rows)
    for secret in (
        "김강사",
        "김담당",
        "010-1111-2222",
        "033-680-9999",
        "staff@example.org",
        "applicant@example.org",
        "신청서.hwp",
        "상세설명",
    ):
        assert secret not in persisted


@pytest.mark.parametrize(
    "attribute,error_fragment",
    [
        ("page_one_mutation", "stability recheck changed"),
        ("sentinel_mutation", "post-last sentinel changed"),
        ("duplicate_identity", "duplicate source identities"),
        ("header_mutation", "table headers changed"),
    ],
)
def test_list_contract_mutations_fail_closed(
    attribute: str, error_fragment: str
) -> None:
    site = FakeSite()
    setattr(site, attribute, True)
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error_fragment in meta["configured_collection_error"]


@pytest.mark.parametrize(
    "attribute,error_fragment",
    [
        ("detail_title_mismatch", "detail title differs from list"),
        ("application_identity_mismatch", "course-bound public route"),
        ("missing_active_control", "exactly one application control"),
        ("active_on_closed", "non-open course exposes an active"),
        ("unknown_detail_field", "unknown detail field"),
    ],
)
def test_detail_and_application_mutations_fail_closed(
    attribute: str, error_fragment: str
) -> None:
    site = FakeSite()
    setattr(site, attribute, True)
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error_fragment in meta["configured_collection_error"]


def test_official_detail_omissions_use_explicit_auditable_fallbacks() -> None:
    site = FakeSite()
    site.courses["general"][0] = replace(
        site.courses["general"][0],
        fee="",
        schedule="",
    )
    site.courses["information"][0] = replace(
        site.courses["information"][0],
        target="",
    )
    rows, _, meta = _collect(site)

    assert meta["snapshot_complete"] is True
    by_id = {row["provider_course_id"]: row for row in rows}
    general = by_id["general:100"]
    assert general["fee"] == "요금 별도 안내"
    assert general["schedule_raw"] == "시간 별도 안내"
    assert general["venue_name"] == "장소 별도 안내"
    assert general["raw_fields"]["fee_evidence"] == "official_detail_omits_fee"
    assert (
        general["raw_fields"]["schedule_evidence"]
        == "official_detail_omits_schedule"
    )
    information = by_id["information:100"]
    assert information["target"] == "대상 별도 안내"
    assert (
        information["raw_fields"]["target_evidence"]
        == "official_detail_omits_target"
    )


def test_caps_never_return_a_partial_snapshot() -> None:
    site = FakeSite()
    rows, _, meta = _collect(site, max_pages=14)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "14 of 15" in meta["configured_collection_error"]
    assert len(site.calls) == 4

    site = FakeSite()
    rows, _, meta = _collect(site, detail_limit=3)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "3 of 4" in meta["configured_collection_error"]
    assert not any("view.do" in url for _, url, _ in site.calls)


def test_dedupe_cardinality_or_privacy_mutation_fails_closed() -> None:
    site = FakeSite()
    rows, _, meta = _collect(site, dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed official identity" in meta["configured_collection_error"]

    def leak(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        copied = [dict(value) for value in values]
        copied[0]["phone"] = "010-1234-5678"
        return copied

    site = FakeSite()
    rows, _, meta = _collect(site, dedupe_rows=leak)
    assert rows == []
    assert "unexpected persisted keys" in meta["configured_collection_error"]


def test_complete_no_current_snapshot_is_valid_and_fetches_no_details() -> None:
    site = FakeSite(no_current=True)
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["detail_attempts"] == 0
    assert meta["detail_pages"] == 0
    assert not any("view.do" in url for _, url, _ in site.calls)


def test_audited_legacy_reversed_dates_and_blank_waiting_limit_are_lossless() -> None:
    assert goseong._date_range(
        "2026-01-17 ~ 2025-07-18", "education period"
    ) == ("2025-07-18", "2026-01-17")
    assert goseong._parse_capacity("0 / 10 명 (대기 0 / 명)") == (0, 10, 0, 0)


@pytest.mark.parametrize("attribute", ["bad_content_type", "redirected"])
def test_transport_contract_mutations_fail_closed(attribute: str) -> None:
    site = FakeSite()
    setattr(site, attribute, True)
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"]


def test_legacy_tls_adapter_is_verified_and_scoped_to_exact_host() -> None:
    session = goseong._default_session_factory()
    try:
        scoped = session.get_adapter(goseong.GANGWON_GOSEONG_CANONICAL_URL)
        ordinary = session.get_adapter("https://example.org/")
        assert isinstance(scoped, goseong._LegacyCipherAdapter)
        assert not isinstance(ordinary, goseong._LegacyCipherAdapter)
        context = scoped.ssl_context
        assert context.check_hostname is True
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.minimum_version == ssl.TLSVersion.TLSv1_2
        assert context.maximum_version == ssl.TLSVersion.TLSv1_2
        assert goseong.GANGWON_GOSEONG_LEGACY_CIPHER in {
            cipher["name"] for cipher in context.get_ciphers()
        }
    finally:
        session.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout": 0},
        {"max_pages": True},
        {"detail_limit": -1},
        {"max_workers": 0},
        {"today": "not-a-date"},
    ],
)
def test_invalid_runtime_contracts_fail_before_collection(kwargs: dict[str, Any]) -> None:
    site = FakeSite()
    rows, _, meta = _collect(site, **kwargs)
    assert rows == []
    assert meta["configured_collection_error"]
    assert site.calls == []
