from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
import threading
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from Crawler import municipal_gwangju_namgu as namgu


TARGET = {
    "provider": namgu.GWANGJU_NAMGU_PROVIDER,
    "url": namgu.GWANGJU_NAMGU_CANDIDATE_URL,
}


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    start: str
    end: str
    branch: str
    fee: str = "무료"
    apply_start: str = "2026-07-01"
    apply_end: str = "2026-07-31"
    target: str = "남구 주민"
    method: str = "인터넷 접수"


class Response:
    def __init__(
        self,
        html: str,
        url: str,
        *,
        content_type: str = "text/html;charset=UTF-8",
        history: tuple[Any, ...] = (),
    ) -> None:
        self.status_code = 200
        self.content = html.encode("utf-8")
        self.url = url
        self.headers = {"Content-Type": content_type}
        self.history = history


class DummySession:
    def close(self) -> None:
        return None


def _courses(no_current: bool = False) -> list[Course]:
    current = Course(
        "101",
        "시민 디지털 역량 과정",
        "2026-07-22",
        "2026-08-20",
        "남구 평생학습관",
    )
    if no_current:
        current = replace(
            current,
            start="2020-01-01",
            end="2020-01-31",
            apply_start="2019-12-01",
            apply_end="2019-12-20",
        )
    rows = [current]
    for offset in range(1, 11):
        rows.append(
            Course(
                str(101 - offset),
                f"과거 평생학습 {offset}",
                "2020-01-01",
                "2020-01-31",
                "남구 평생학습관",
                apply_start="2019-12-01",
                apply_end="2019-12-20",
            )
        )
    rows.append(
        Course(
            "90",
            namgu.GWANGJU_NAMGU_PLACEHOLDER_TITLE,
            "2018-08-27",
            "2018-08-27",
            "",
            fee="",
            apply_start="2018-08-01",
            apply_end="2018-08-02",
        )
    )
    return rows


def _list_row(course: Course, *, identity: str | None = None) -> str:
    href = "/lecture.es?" + urlencode(
        {
            "mid": namgu.GWANGJU_NAMGU_LIFELONG_MID,
            "act": "view",
            "seq": identity or course.identity,
        }
    )
    return f"""
      <tr>
        <td class="TxtL"><a href="{escape(href)}">{escape(course.title)}</a></td>
        <td>{course.start.replace("-", ".")}~{course.end.replace("-", ".")}</td>
        <td>{escape(course.branch)}</td>
        <td>{escape(course.fee)}</td>
      </tr>
    """


def _lifelong_list_html(
    rows: list[Course],
    page: int,
    *,
    first_recheck: bool = False,
    last_recheck: bool = False,
    sentinel_mutation: bool = False,
    duplicate_identity: bool = False,
    header_mutation: bool = False,
) -> str:
    total = len(rows)
    last = (total + 9) // 10
    start = (page - 1) * 10
    visible = list(rows[start : start + 10]) if page <= last else []
    if first_recheck and visible:
        visible[0] = replace(visible[0], title=visible[0].title + " 변경")
    if last_recheck and visible:
        visible[0] = replace(visible[0], title=visible[0].title + " 변경")
    if sentinel_mutation and page == last + 1:
        visible = [rows[-1]]
    body: list[str] = []
    for index, course in enumerate(visible):
        forced = rows[0].identity if duplicate_identity and page == 2 and index == 0 else None
        body.append(_list_row(course, identity=forced))
    if not visible:
        body.append('<tr><td colspan="4">결과가 없습니다.</td></tr>')
    headers = ["강좌명", "강좌기간", "교육기관", "수강료"]
    if header_mutation:
        headers[-1] = "비용"
    active = min(page, last)
    return f"""
      <html><head><title>주민 맞춤형 평생학습 강좌 | 평생학습도시 남구</title></head>
      <body><div id="content_detail">
        <div class="program">
          <p class="page_info">ㆍ총 <strong class="txt_bold">{total}</strong>건의 강좌가 있습니다.</p>
          <table class="tstyle_list"><thead><tr>
            {"".join(f"<th>{header}</th>" for header in headers)}
          </tr></thead><tbody>{"".join(body)}</tbody></table>
        </div>
        <div class="board_pager">
          <a class="pageNow"><strong>{active}</strong></a>
          <a class="pageLast" href="/lecture.es?mid={namgu.GWANGJU_NAMGU_LIFELONG_MID}&amp;nPage={last}">마지막</a>
        </div>
      </div></body></html>
    """


def _detail_html(
    course: Course,
    *,
    title_mismatch: bool = False,
    missing_control: bool = False,
    control_identity_mismatch: bool = False,
    unknown_field: bool = False,
) -> str:
    title = course.title + (" 변경" if title_mismatch else "")
    identity = str(int(course.identity) + 1) if control_identity_mismatch else course.identity
    control = ""
    if not missing_control:
        href = "/lecture.es?" + urlencode(
            {
                "mid": namgu.GWANGJU_NAMGU_LIFELONG_MID,
                "seq": identity,
                "act": "mem_form",
            }
        )
        control = f'<a href="{escape(href)}">교육 신청</a>'
    unknown = "<tr><th>비밀필드</th><td>비밀값</td></tr>" if unknown_field else ""
    return f"""
      <html><head><title>{escape(title)} | 평생학습도시 남구</title></head><body>
      <div id="content_detail">
        <table class="tstyle_view">
          <tr><th class="title" colspan="4">{escape(title)}</th></tr>
          <tr><th>강좌기간</th><td>{course.start}~{course.end}</td>
              <th>신청기간</th><td>{course.apply_start}~{course.apply_end}</td></tr>
          <tr><th>교육기관</th><td>{escape(course.branch)}</td>
              <th>대상</th><td>{escape(course.target)}</td></tr>
          <tr><th>접수방법</th><td>{escape(course.method)}</td>
              <th>수강료</th><td>{escape(course.fee)}</td></tr>
          <tr><th>문의사항</th><td colspan="3">062-607-2431</td></tr>
          <tr><th>첨부파일</th><td colspan="3"><a href="/download.es">계획서.hwp</a></td></tr>
          {unknown}
          <tr><th colspan="4">기타</th></tr>
          <tr><td colspan="4">강사 홍길동, staff@example.org, 상세 강좌 내용</td></tr>
        </table>
        <div class="buttons">{control}</div>
      </div></body></html>
    """


def _info_card(
    title: str,
    apply_period: str,
    education_period: str,
    schedule: str,
    status: str,
    *,
    control: bool = False,
    bad_control: bool = False,
) -> str:
    anchor = ""
    if control:
        if bad_control:
            href = "/education.es?mid=a10104010300&act=form"
        else:
            href = "/education.es?mid=a10104010300&act=form&edu_seq=9001"
        anchor = f'<a href="{escape(href)}">신청하기</a>'
    state_class = {
        "접수대기": "state01",
        "접수중": "state02",
        "접수마감": "state03",
    }[status]
    return f"""
      <li><div class="text"><div class="textTitle">{escape(title)}</div>
        <ul class="textEdu">
          <li><strong>장소 : </strong> 남구청 교육장</li>
          <li><strong>접수기간 : </strong> {apply_period}</li>
          <li><strong>교육기간 : </strong> {education_period}</li>
          <li><strong>시간 : </strong> {schedule}</li>
          <li><strong>인원 : </strong> 접수 0 (정원 35)</li>
        </ul><ul class="btnArea"><li class="{state_class}">{status}{anchor}</li></ul>
      </div></li>
    """


def _information_html(
    *,
    no_current: bool = False,
    mutation: bool = False,
    missing_open_control: bool = False,
    active_on_scheduled: bool = False,
    bad_scheduled_control: bool = False,
    bad_open_control: bool = False,
) -> str:
    if no_current:
        cards = "등록된 교육일정이 없습니다."
    else:
        first_title = "동영상 편집" + (" 변경" if mutation else "")
        cards = _info_card(
            first_title,
            "2026-07-27 ~ 2026-07-31",
            "2026-08-03 ~ 2026-08-24",
            "13:00 ~ 15:00",
            "접수대기",
            control=active_on_scheduled or bad_scheduled_control,
            bad_control=bad_scheduled_control,
        )
        cards += _info_card(
            "AI 활용",
            "2026-07-10 ~ 2026-07-31",
            "2026-08-03 ~ 2026-08-24",
            "15:30 ~ 17:30",
            "접수중",
            control=not missing_open_control,
            bad_control=bad_open_control,
        )
    return f"""
      <html><head><title>접수목록 | 교육 신청 : 전남광주통합특별시 남구</title></head>
      <body><div id="content_detail"><div class="eduContainer">
        <h4 class="h4">교육일정 및 신청</h4>
        <div id="eduprogram_responsive"><ul class="eduList">{cards}</ul></div>
      </div></div></body></html>
    """


class FakeSite:
    def __init__(self, *, no_current: bool = False) -> None:
        self.courses = _courses(no_current=no_current)
        self.no_current = no_current
        self.calls: list[str] = []
        self.lock = threading.Lock()
        self.page_calls: dict[int, int] = {}
        self.info_calls = 0
        self.page_one_mutation = False
        self.last_page_mutation = False
        self.sentinel_mutation = False
        self.duplicate_identity = False
        self.header_mutation = False
        self.information_mutation = False
        self.missing_open_control = False
        self.active_on_scheduled = False
        self.bad_scheduled_control = False
        self.bad_open_control = False
        self.detail_title_mismatch = False
        self.missing_detail_control = False
        self.detail_control_identity_mismatch = False
        self.unknown_detail_field = False
        self.bad_content_type = False
        self.redirected = False

    def fetcher(self, session: Any, url: str, timeout: int) -> Response:
        del session, timeout
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        with self.lock:
            self.calls.append(url)
        if parsed.hostname == namgu.GWANGJU_NAMGU_MAIN_HOST:
            with self.lock:
                self.info_calls += 1
                call = self.info_calls
            html = _information_html(
                no_current=self.no_current,
                mutation=self.information_mutation and call > 1,
                missing_open_control=self.missing_open_control,
                active_on_scheduled=self.active_on_scheduled,
                bad_scheduled_control=self.bad_scheduled_control,
                bad_open_control=self.bad_open_control,
            )
        elif query.get("act") == ["view"]:
            identity = query["seq"][0]
            course = next(row for row in self.courses if row.identity == identity)
            html = _detail_html(
                course,
                title_mismatch=self.detail_title_mismatch,
                missing_control=self.missing_detail_control,
                control_identity_mismatch=self.detail_control_identity_mismatch,
                unknown_field=self.unknown_detail_field,
            )
        else:
            assert query.get("act") != ["mem_form"], "application form was fetched"
            page = int(query["nPage"][0])
            with self.lock:
                self.page_calls[page] = self.page_calls.get(page, 0) + 1
                call = self.page_calls[page]
            last = (len(self.courses) + 9) // 10
            html = _lifelong_list_html(
                self.courses,
                page,
                first_recheck=self.page_one_mutation and page == 1 and call > 1,
                last_recheck=self.last_page_mutation and page == last and call > 1,
                sentinel_mutation=self.sentinel_mutation,
                duplicate_identity=self.duplicate_identity,
                header_mutation=self.header_mutation,
            )
        return Response(
            html,
            url,
            content_type=("application/json" if self.bad_content_type else "text/html"),
            history=((object(),) if self.redirected else ()),
        )


def _collect(site: FakeSite, **kwargs: Any):
    crawl_today = kwargs.pop("today", "2026-07-21")
    return namgu.collect_gwangju_namgu_education(
        TARGET,
        today=crawl_today,
        session_factory=DummySession,
        fetcher=site.fetcher,
        **kwargs,
    )


def test_target_alias_and_owner_boundaries_are_exact() -> None:
    assert namgu.is_gwangju_namgu_education_target(TARGET)
    assert namgu.is_gwangju_namgu_education_target({**TARGET, "url": namgu.GWANGJU_NAMGU_INFORMATION_URL})
    assert namgu.is_gwangju_namgu_education_target({**TARGET, "url": namgu.GWANGJU_NAMGU_LIFELONG_URL})
    assert not namgu.is_gwangju_namgu_excluded_target(TARGET)
    assert not namgu.is_gwangju_namgu_education_target({**TARGET, "provider": "MUNI_OTHER_OWNER"})
    for unsafe in (
        namgu.GWANGJU_NAMGU_CANDIDATE_URL.replace("https://", "http://"),
        namgu.GWANGJU_NAMGU_CANDIDATE_URL + "&nPage=1",
        namgu.GWANGJU_NAMGU_CANDIDATE_URL + "#fragment",
        namgu.GWANGJU_NAMGU_CANDIDATE_URL.replace("www.namgu.gwangju.kr", "user@www.namgu.gwangju.kr"),
        namgu.GWANGJU_NAMGU_CANDIDATE_URL.replace("www.namgu.gwangju.kr", "www.namgu.gwangju.kr:443"),
    ):
        assert not namgu.is_gwangju_namgu_education_target({**TARGET, "url": unsafe})
    assert all(namgu.is_gwangju_namgu_alias_target({"url": url}) for url in namgu.GWANGJU_NAMGU_ALIAS_URLS)
    assert all(
        namgu.is_gwangju_namgu_excluded_target({"url": url})
        for url in (
            *namgu.GWANGJU_NAMGU_EXCLUDED_URLS.values(),
            *namgu.GWANGJU_NAMGU_SEPARATE_OWNER_URLS.values(),
        )
    )
    assert namgu.GWANGJU_NAMGU_RESIDENT_CENTRE_BRANCHES == (
        "양림동",
        "방림1동",
        "방림2동",
        "봉선1동",
        "봉선2동",
        "사직동",
        "월산동",
        "월산4동",
        "월산5동",
        "백운1동",
        "백운2동",
        "주월1동",
        "주월2동",
        "진월동",
        "효덕동",
        "송암동",
        "대촌동",
    )
    assert namgu.GWANGJU_NAMGU_LIBRARY_BRANCHES == (
        "문화정보도서관",
        "푸른길도서관",
        "청소년도서관",
        "효천어울림도서관",
    )


def test_complete_snapshot_paginates_details_and_never_fetches_application() -> None:
    site = FakeSite()
    rows, parser, meta = _collect(site)

    assert parser == namgu.GWANGJU_NAMGU_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["source_totals"] == {"lifelong": 12, "information": 2}
    assert meta["source_pages"] == {"lifelong": 2, "information": 1}
    assert meta["source_total"] == 14
    assert meta["lifelong_unique_identities"] == 12
    assert meta["lifelong_placeholder_rows"] == 1
    assert meta["required_list_requests"] == 7
    assert meta["list_requests"] == 7
    assert meta["sentinel_requests"] == 1
    assert meta["stability_rechecks"] == 3
    assert meta["detail_attempts"] == 1
    assert meta["detail_pages"] == 1
    assert meta["current_future_counts"] == {"lifelong": 1, "information": 2}
    assert meta["public_application_control_count"] == 2
    assert meta["status_counts"] == {"OPEN": 2, "SCHEDULED": 1}
    assert len(rows) == 3
    assert {row["title"] for row in rows} == {
        "시민 디지털 역량 과정",
        "동영상 편집",
        "AI 활용",
    }
    assert {row["branch"] for row in rows} == {"남구 평생학습관", "남구청 교육장"}
    assert {row["provider_course_id"].split(":", 1)[0] for row in rows} == {
        "lifelong",
        "information",
    }
    assert all(row["municipality_code"] == "1227000000" for row in rows)
    assert all(row["municipality_name"] == "전남광주통합특별시 남구" for row in rows)
    assert all(row["raw_fields"]["detail_verified"] for row in rows)
    assert all(row["raw_fields"]["application_control_verified"] for row in rows)
    assert len({row["raw_url"] for row in rows}) == len(rows)

    fetched_queries = [parse_qs(urlparse(url).query) for url in site.calls]
    assert not any(query.get("act") == ["mem_form"] for query in fetched_queries)
    assert not any(query.get("act") == ["form"] for query in fetched_queries)
    persisted = repr(rows)
    for secret in (
        "홍길동",
        "062-607-2431",
        "staff@example.org",
        "계획서.hwp",
        "상세 강좌 내용",
    ):
        assert secret not in persisted


@pytest.mark.parametrize(
    "attribute,error_fragment",
    [
        ("page_one_mutation", "page-one stability recheck changed"),
        ("last_page_mutation", "last-page stability recheck changed"),
        ("sentinel_mutation", "post-last page"),
        ("duplicate_identity", "duplicate lifelong source identities"),
        ("header_mutation", "table headers changed"),
        ("information_mutation", "information-education stability recheck changed"),
    ],
)
def test_list_contract_mutations_fail_closed(attribute: str, error_fragment: str) -> None:
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
        ("missing_detail_control", "exactly one application control required"),
        ("detail_control_identity_mismatch", "application control is not course-bound"),
        ("unknown_detail_field", "unknown detail field"),
        ("missing_open_control", "exactly one application control"),
        ("bad_scheduled_control", "application control is not course-bound"),
        ("bad_open_control", "application control is not course-bound"),
    ],
)
def test_detail_and_application_mutations_fail_closed(attribute: str, error_fragment: str) -> None:
    site = FakeSite()
    setattr(site, attribute, True)
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error_fragment in meta["configured_collection_error"]


def test_non_open_information_route_is_verified_but_not_published() -> None:
    site = FakeSite()
    site.active_on_scheduled = True

    rows, _, meta = _collect(site)

    assert meta["snapshot_complete"] is True
    scheduled = next(row for row in rows if row["status"] == "SCHEDULED")
    assert scheduled["application_url"] == ""
    assert "act=form" in scheduled["raw_url"]
    assert scheduled["reservation_available"] is False
    assert scheduled["raw_fields"]["application_control_present"] is True
    assert (
        scheduled["raw_fields"]["application_control_contract"]
        == "status_gated_same_host_education_form_course_identity"
    )


def test_caps_never_return_a_partial_snapshot() -> None:
    site = FakeSite()
    rows, _, meta = _collect(site, max_pages=6)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "6 of 7" in meta["configured_collection_error"]
    assert len(site.calls) == 2

    site = FakeSite()
    rows, _, meta = _collect(site, detail_limit=0)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "0 of 1" in meta["configured_collection_error"]
    assert len(site.calls) == 7
    assert not any(parse_qs(urlparse(url).query).get("act") == ["view"] for url in site.calls)


def test_dedupe_cardinality_and_privacy_mutations_fail_closed() -> None:
    site = FakeSite()
    rows, _, meta = _collect(site, dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed official identity cardinality" in meta["configured_collection_error"]

    def leak(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        copied = [dict(row) for row in values]
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
    assert meta["current_future_counts"] == {"lifelong": 0, "information": 0}
    assert meta["detail_attempts"] == 0
    assert meta["detail_pages"] == 0
    assert not any(parse_qs(urlparse(url).query).get("act") == ["view"] for url in site.calls)


@pytest.mark.parametrize("attribute", ["bad_content_type", "redirected"])
def test_transport_contract_mutations_fail_closed(attribute: str) -> None:
    site = FakeSite()
    setattr(site, attribute, True)
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"]


def test_fetch_allowlist_rejects_forms_and_unrelated_routes() -> None:
    assert namgu._allowed_request_url(namgu.GWANGJU_NAMGU_INFORMATION_URL)
    assert namgu._allowed_request_url(namgu._lifelong_page_url(1))
    assert namgu._allowed_request_url(namgu._lifelong_detail_url("101"))
    assert not namgu._allowed_request_url(
        "https://lll.namgu.gwangju.kr/lecture.es?mid=a10202010100&seq=101&act=mem_form"
    )
    assert not namgu._allowed_request_url(
        "https://www.namgu.gwangju.kr/education.es?mid=a10104010300&act=form&edu_seq=9001"
    )
    assert not namgu._allowed_request_url("https://lib.namgu.gwangju.kr/main/clturReq/1")


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
