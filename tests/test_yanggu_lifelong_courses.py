from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_yanggu as yanggu


@dataclass
class Target:
    provider: str = yanggu.YANGGU_PROVIDER
    url: str = yanggu.YANGGU_URL


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


COURSES: tuple[dict[str, Any], ...] = (
    {
        "identity": "101",
        "audience_group": "성인대상",
        "category": "문화예술교육",
        "title": "생활도예 교실",
        "instructor": "김양구",
        "capacity": 12,
        "applied": 2,
        "target": "성인",
        "schedule": "월<br>10:00~12:00<br>2시간",
        "venue": "101",
        "plan_text": "2026 강의계획서",
        "has_plan": True,
    },
    {
        "identity": "102",
        "audience_group": "성인대상",
        "category": "인문교양교육",
        "title": "스마트폰 활용",
        "instructor": "이학습",
        "capacity": 10,
        "applied": 1,
        "target": "성인",
        "schedule": "화, 목<br>14:00~16:00<br>2시간",
        "venue": "2층디지털교육실",
        "plan_text": "강의계획서",
        "has_plan": True,
    },
    {
        "identity": "201",
        "audience_group": "아동대상",
        "category": "문화예술교육",
        "title": "어린이 미술교실",
        "instructor": "박어린이",
        "capacity": 8,
        "applied": 4,
        "target": "초등학생",
        "schedule": "수, 금<br>16:20~17:00<br>40분",
        "venue": "어린이교육실",
        "plan_text": "없음",
        "has_plan": False,
    },
)


TABLES: tuple[tuple[str, str], ...] = (
    ("total", "example8ad"),
    ("recruit", "example8ae"),
    ("ongoing", "example8af"),
    ("short", "example8ag"),
    ("adult", "example8ah"),
    ("child", "example8ai"),
)


def _course(identity: str) -> dict[str, Any]:
    return deepcopy(next(row for row in COURSES if row["identity"] == identity))


def _landing_html() -> str:
    return """
    <!doctype html>
    <html lang="ko"><head>
      <title>양구군 평생학습관</title>
      <meta property="og:title" content="양구군 평생학습관 홈페이지">
      <meta property="og:url" content="https://www.yanggu.go.kr/lll">
    </head><body>
      <h1>양구군 평생학습관</h1>
      <nav>
        <a href="pageview.do?url=sub02a&amp;keyvalue=sub02#Book2">수강신청</a>
      </nav>
      <footer>강원특별자치도 양구군 양구읍 박수근로 286-5</footer>
    </body></html>
    """


def _overview_html() -> str:
    return """
    <table class="table table-bordered f18 text-center bg_fff">
      <tbody>
        <tr style="border-top:2px solid #0278cb">
          <th>프로그램 운영시간</th>
          <td>
            <p><span>상반기 3. 16.(월) ~ 6. 26.(금) / 15주 과정</span></p>
            <p><span>하반기 8. 10.(월) ~ 11. 23.(월) / 15주 과정</span></p>
            <p><span>※ 법정 공휴일 및 평생학습 축제시 휴강</span></p>
          </td>
        </tr>
        <tr><th>수강료</th><td>대면강좌 40,000원 / 비대면 강좌 20,000원</td></tr>
        <tr><th>학습시간</th><td>성인평생학습 50분, 아동평생학습 40분</td></tr>
        <tr><th>면제대상</th><td>기초생활수급자, 저소득한부모, 장애인, 다자녀</td></tr>
      </tbody>
    </table>
    <table class="table table-bordered f18 text-center bg_fff">
      <tbody>
        <tr style="border-top:2px solid #0278cb">
          <th>구분</th><th>상반기<br>기간 및 시간</th>
          <th>하반기<br>기간 및 시간</th><th>비고</th>
        </tr>
        <tr>
          <td>모집기간</td>
          <td><p>1. 26.(월) ~ 2. 6.(금)<br>10:00 ~ 18:00</p></td>
          <td><p>7. 20.(월) ~ 7. 31.(금)<br>10:00 ~ 18:00</p></td>
          <td>인터넷 접수 후 추첨(대기자 선착순)</td>
        </tr>
        <tr>
          <td>추첨 발표<br>(전자추첨)</td>
          <td>2. 10.(화) 10:00</td><td>8. 3.(월) 10:00</td><td>개인별 문자 발송</td>
        </tr>
        <tr>
          <td>수강등록 및<br>입금기간</td>
          <td>2. 10.(화) ~ 2. 12.(목)</td><td>8. 4.(화) ~ 8. 6.(목)</td>
          <td>가상계좌 입금</td>
        </tr>
        <tr>
          <td>미달과목<br>학습자 모집</td>
          <td>2. 23.(월) ~ 2. 25.(수)</td><td>8. 10.(월) ~ 8. 14.(금)</td>
          <td>온라인/방문 신청 가능</td>
        </tr>
        <tr>
          <td>수강료 면제대상<br>신청기간</td>
          <td>2. 25.(수) ~ 2. 27.(금)</td><td>8. 19.(수) ~ 8. 21.(금)</td>
          <td>증명서 제출</td>
        </tr>
        <tr>
          <td>운영기간<br>주수</td>
          <td><p>3. 16.(월) ~ 6. 26.(금)<br>15주</p></td>
          <td><p>8. 10.(월) ~ 11. 23.(월)<br>15주</p></td>
          <td>법정 공휴일 및 평생학습 축제시 휴강</td>
        </tr>
      </tbody>
    </table>
    """


def _course_row(course: dict[str, Any], *, number: int, total: bool) -> str:
    identity = course["identity"]
    capacity = str(course["capacity"])
    if not total:
        capacity = f"{capacity} / {course['applied']}"
    if course["has_plan"]:
        plan = (
            '<a class="btn btn-success btn-xs" '
            f'href="../../lll/yglll/bbs_download.do?dwnfilea={identity}">'
            f"{course['plan_text']}</a>"
        )
    else:
        plan = "없음"
    return f"""
    <tr>
      <td class="text-center align-middle f16">{number}</td>
      <td class="text-center align-middle f16">{course['audience_group']}</td>
      <td class="text-center align-middle f16">{course['category']}</td>
      <td class="text-center align-middle f16">{course['title']}</td>
      <td class="text-center align-middle f16">{course['instructor']}</td>
      <td class="text-center align-middle f16">{capacity}</td>
      <td class="text-center align-middle f16">{course['target']}</td>
      <td class="text-center align-middle f16">{course['schedule']}</td>
      <td class="text-center align-middle f16">{course['venue']}</td>
      <td class="text-center align-middle f16">{plan}</td>
      <td class="text-center align-middle f16">
        <a class="btn btn-xs btn-warning"
           href="pageview.do?url=sub09ak&amp;keyvalue=sub09&amp;idx={identity}#Book1">수강신청</a>
      </td>
    </tr>
    """


def _course_table(table_id: str, courses: list[dict[str, Any]]) -> str:
    total = table_id == "example8ad"
    capacity_header = "정원" if total else "정원/신청"
    body = "".join(
        _course_row(course, number=number, total=total)
        for number, course in enumerate(courses)
    )
    return f"""
    <table class="table table-bordered table-striped" id="{table_id}" style="width:100%">
      <thead><tr style="border-top:2px solid #0278cb">
        <th>번호</th><th>분류</th><th>구분</th><th>강좌명</th><th>강사명</th>
        <th>{capacity_header}</th><th>대상</th><th>시간</th><th>교육장소</th>
        <th>강의계획서</th><th>수강신청</th>
      </tr></thead>
      <tbody>{body}</tbody>
    </table>
    """


def _default_table_rows() -> dict[str, list[dict[str, Any]]]:
    all_rows = [_course("101"), _course("102"), _course("201")]
    return {
        "total": deepcopy(all_rows),
        "recruit": deepcopy(all_rows),
        "ongoing": [],
        "short": [],
        "adult": [deepcopy(row) for row in all_rows if row["audience_group"] == "성인대상"],
        "child": [deepcopy(row) for row in all_rows if row["audience_group"] == "아동대상"],
    }


def _program_html(
    table_rows: dict[str, list[dict[str, Any]]] | None = None,
) -> str:
    rows = table_rows or _default_table_rows()
    tables = "".join(
        _course_table(table_id, rows[name]) for name, table_id in TABLES
    )
    return f"""
    <!doctype html>
    <html lang="ko"><head>
      <title>양구군 평생학습관</title>
      <meta property="og:type" content="양구군 평생학습관 홈페이지">
      <meta property="og:title" content="양구군 평생학습관 홈페이지">
      <meta property="og:url" content="https://www.yanggu.go.kr/lll">
    </head><body>
      <h1>양구군 평생학습관</h1>
      <nav>
        <a href="pageview.do?url=sub02a&amp;keyvalue=sub02">교육프로그램</a>
        <a href="pageview.do?url=sub02a&amp;keyvalue=sub02#Book1">교육운영 안내</a>
        <a href="pageview.do?url=sub02a&amp;keyvalue=sub02#Book2">프로그램 안내</a>
        <a href="pageview.do?url=sub02a&amp;keyvalue=sub02#Book2">수강신청</a>
      </nav>
      <main id="contents">
        <h2>교육프로그램</h2>
        <section id="Book1">{_overview_html()}</section>
        <section id="Book2">{tables}</section>
      </main>
      <footer>강원특별자치도 양구군 양구읍 박수근로 286-5</footer>
    </body></html>
    """


def _detail_html(
    identity: str,
    *,
    form_name: str = "join_form",
    method: str = "post",
    action: str = "pageview.do?url=sub09am&amp;keyvalue=sub09#Book1",
    join: str = "1",
    form_identity: str | None = None,
    include_second_checkbox: bool = True,
    submit_text: str = "수강신청",
) -> str:
    second_checkbox = (
        '<input type="checkbox" name="tap_accept2" value="1">'
        if include_second_checkbox
        else ""
    )
    return f"""
    <!doctype html>
    <html lang="ko"><head>
      <title>양구군 평생학습관</title>
      <meta property="og:title" content="양구군 평생학습관 홈페이지">
      <meta property="og:url" content="https://www.yanggu.go.kr/lll">
    </head><body>
      <h1>양구군 평생학습관</h1>
      <nav><a href="pageview.do?url=sub02a&amp;keyvalue=sub02#Book2">수강신청</a></nav>
      <form name="{form_name}" method="{method}" action="{action}" role="form">
        <input type="hidden" name="ptSignature" value="fixture-signature">
        <input type="hidden" name="join" value="{join}">
        <input type="hidden" name="idx" value="{form_identity or identity}">
        <input type="checkbox" name="tap_accept1" value="1">
        {second_checkbox}
        <button type="submit">{submit_text}</button>
        <button type="reset">취소하기</button>
      </form>
      <footer>강원특별자치도 양구군 양구읍 박수근로 286-5</footer>
    </body></html>
    """


ProgramFactory = Callable[[int], str]
DetailHook = Callable[[str], str]


class FixtureSite:
    def __init__(
        self,
        *,
        program_factory: ProgramFactory | None = None,
        detail_hook: DetailHook | None = None,
    ) -> None:
        self.program_factory = program_factory
        self.detail_hook = detail_hook
        self.calls: list[str] = []
        self.program_calls = 0
        self.sessions: list[FakeSession] = []

    def session_factory(self) -> FakeSession:
        current = FakeSession()
        self.sessions.append(current)
        return current

    def fetcher(self, _session: Any, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 2
        self.calls.append(url)
        if url == yanggu.YANGGU_URL:
            return BeautifulSoup(_landing_html(), "lxml")
        if url == yanggu.yanggu_program_url():
            self.program_calls += 1
            html = (
                self.program_factory(self.program_calls)
                if self.program_factory
                else _program_html()
            )
            return BeautifulSoup(html, "lxml")

        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if (
            parsed.path == "/lll/yglll/pageview.do"
            and query.get("url") == ["sub09ak"]
            and query.get("keyvalue") == ["sub09"]
            and len(query.get("idx", [])) == 1
        ):
            identity = query["idx"][0]
            if identity not in {row["identity"] for row in COURSES}:
                raise AssertionError(f"unknown fixture identity: {identity}")
            html = self.detail_hook(identity) if self.detail_hook else _detail_html(identity)
            return BeautifulSoup(html, "lxml")
        raise AssertionError(f"unexpected fixture URL: {url}")


def _collect(site: FixtureSite, **kwargs: Any):
    return yanggu.collect_yanggu_lifelong_courses(
        Target(),
        timeout=2,
        max_pages=kwargs.pop("max_pages", 1),
        detail_limit=kwargs.pop("detail_limit", 100),
        fetcher=site.fetcher,
        session_factory=site.session_factory,
        dedupe_rows=kwargs.pop("dedupe_rows", None),
        today=kwargs.pop("today", "2026-07-20"),
        max_workers=kwargs.pop("max_workers", 1),
        **kwargs,
    )


def _mutate_cell(
    html: str,
    table_id: str,
    row_index: int,
    cell_index: int,
    value: str,
) -> str:
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select(f"#{table_id} tbody > tr")
    cells = rows[row_index].find_all("td", recursive=False)
    cells[cell_index].clear()
    cells[cell_index].append(value)
    return str(soup)


def _copy_row(html: str, source_id: str, destination_id: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    row = soup.select_one(f"#{source_id} tbody > tr")
    destination = soup.select_one(f"#{destination_id} tbody")
    assert row is not None and destination is not None
    destination.append(deepcopy(row))
    return str(soup)


def _remove_row(html: str, table_id: str, row_index: int) -> str:
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select(f"#{table_id} tbody > tr")
    rows[row_index].decompose()
    return str(soup)


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        ("WRONG", yanggu.YANGGU_URL),
        (yanggu.YANGGU_PROVIDER, "http://yanggu.go.kr/lll/yglll/index.do"),
        (yanggu.YANGGU_PROVIDER, "https://www.yanggu.go.kr/lll/yglll/index.do"),
        (yanggu.YANGGU_PROVIDER, "https://yanggu.go.kr/lll/yglll/index.do/"),
        (yanggu.YANGGU_PROVIDER, "https://yanggu.go.kr:443/lll/yglll/index.do"),
        (yanggu.YANGGU_PROVIDER, "https://yanggu.go.kr:bad/lll/yglll/index.do"),
        (yanggu.YANGGU_PROVIDER, "https://user@yanggu.go.kr/lll/yglll/index.do"),
        (yanggu.YANGGU_PROVIDER, "https://yanggu.go.kr/lll/yglll/index.do?x=1"),
        (yanggu.YANGGU_PROVIDER, "https://yanggu.go.kr/lll/yglll/index.do#Book1"),
        (yanggu.YANGGU_PROVIDER, "https://yanggu.go.kr.evil.test/lll/yglll/index.do"),
    ],
)
def test_exact_target_boundary(provider: str, url: str) -> None:
    assert not yanggu.is_yanggu_target(Target(provider=provider, url=url))
    assert yanggu.is_yanggu_target(Target())


def test_reviewed_constants_and_url_builders() -> None:
    assert yanggu.YANGGU_PROVIDER == "MUNI_YANGGU_GO_KR_19704EDA"
    assert yanggu.YANGGU_CANDIDATE_ID == "MUNI_IR_03B472D50913"
    assert yanggu.YANGGU_URL == "https://yanggu.go.kr/lll/yglll/index.do"
    assert yanggu.yanggu_program_url() == (
        "https://yanggu.go.kr/lll/yglll/pageview.do?url=sub02a&keyvalue=sub02"
    )
    assert yanggu.yanggu_application_url("../101") == ""
    parsed = urlparse(yanggu.yanggu_application_url("101"))
    assert parsed.scheme == "https"
    assert parsed.netloc == "yanggu.go.kr"
    assert parsed.path == "/lll/yglll/pageview.do"
    assert parse_qs(parsed.query) == {
        "url": ["sub09ak"],
        "keyvalue": ["sub09"],
        "idx": ["101"],
    }
    assert parsed.fragment == "Book1"


def test_complete_snapshot_validates_all_six_views_and_every_application_detail() -> None:
    site = FixtureSite()
    rows, parser, meta = _collect(site)

    assert parser == yanggu.YANGGU_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["source_rows"] == 3
    assert meta["current_count"] == meta["returned_count"] == 3
    assert meta["landing_requests"] == 1
    assert meta["program_requests"] == 2
    assert meta["detail_attempts"] == meta["detail_pages"] == 3
    assert meta["pages"] == 1
    assert meta["request_count"] == 6
    assert meta["table_counts"] == {
        "example8ad": 3,
        "example8ae": 3,
        "example8af": 0,
        "example8ag": 0,
        "example8ah": 2,
        "example8ai": 1,
    }
    assert meta["semester"] == "하반기"
    assert meta["current_status_counts"] == {"OPEN": 3}
    assert meta["classification_counts"] == {"성인대상": 2, "아동대상": 1}
    assert meta["reservation_discovery_links"] == 3
    assert {row["title"] for row in rows} == {
        "생활도예 교실",
        "스마트폰 활용",
        "어린이 미술교실",
    }
    assert {row["status"] for row in rows} == {"OPEN"}
    assert {row["start_date"] for row in rows} == {"2026-08-10"}
    assert {row["end_date"] for row in rows} == {"2026-11-23"}
    assert all(row["domain_category"] == "교육" for row in rows)
    assert all(row["application_url"] for row in rows)
    assert site.program_calls == 2
    assert site.sessions and all(current.closed for current in site.sessions)


@pytest.mark.parametrize("mode", ["mismatch", "duplicate", "partition"])
def test_cross_table_contract_changes_fail_closed(mode: str) -> None:
    def program(_call: int) -> str:
        html = _program_html()
        if mode == "mismatch":
            return _mutate_cell(html, "example8ae", 0, 3, "변경된 강좌명")
        if mode == "duplicate":
            return _copy_row(html, "example8ad", "example8ad")
        return _remove_row(html, "example8ai", 0)

    rows, _, meta = _collect(FixtureSite(program_factory=program))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"]


def test_short_table_must_remain_empty_for_the_reviewed_snapshot() -> None:
    def program(_call: int) -> str:
        return _copy_row(_program_html(), "example8ae", "example8ag")

    rows, _, meta = _collect(FixtureSite(program_factory=program))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "short" in meta["configured_collection_error"].lower() or "단기" in meta[
        "configured_collection_error"
    ]


def test_program_page_recheck_detects_mid_traversal_mutation() -> None:
    def program(call: int) -> str:
        html = _program_html()
        if call == 2:
            return _mutate_cell(html, "example8ad", 0, 3, "재확인 중 변경된 강좌")
        return html

    rows, _, meta = _collect(FixtureSite(program_factory=program))
    assert rows == []
    assert meta["snapshot_complete"] is False
    error = meta["configured_collection_error"].lower()
    assert "program recheck" in error
    assert "changed" in error or "differs" in error or "변경" in error


@pytest.mark.parametrize(
    "mode",
    ["identity", "form_name", "method", "action", "join", "checkbox", "submit"],
)
def test_any_application_identity_or_form_contract_change_fails_closed(mode: str) -> None:
    def detail(identity: str) -> str:
        if identity != "101":
            return _detail_html(identity)
        kwargs: dict[str, Any] = {}
        if mode == "identity":
            kwargs["form_identity"] = "999"
        elif mode == "form_name":
            kwargs["form_name"] = "changed_form"
        elif mode == "method":
            kwargs["method"] = "get"
        elif mode == "action":
            kwargs["action"] = "pageview.do?url=changed&amp;keyvalue=sub09#Book1"
        elif mode == "join":
            kwargs["join"] = "0"
        elif mode == "checkbox":
            kwargs["include_second_checkbox"] = False
        elif mode == "submit":
            kwargs["submit_text"] = "확인"
        return _detail_html(identity, **kwargs)

    rows, _, meta = _collect(FixtureSite(detail_hook=detail))
    assert rows == []
    assert meta["snapshot_complete"] is False
    error = meta["configured_collection_error"].lower()
    assert "detail" in error or "application" in error or "신청" in error


def test_page_and_detail_caps_fail_before_any_application_detail() -> None:
    site = FixtureSite()
    rows, _, meta = _collect(site, max_pages=0)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert not any("sub09ak" in url for url in site.calls)

    site = FixtureSite()
    rows, _, meta = _collect(site, detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["detail_attempts"] == 0
    assert not any("sub09ak" in url for url in site.calls)


def test_dedupe_may_not_remove_a_valid_current_course() -> None:
    rows, _, meta = _collect(
        FixtureSite(),
        dedupe_rows=lambda values: values[:-1],
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "dedupe changed complete row count 3 to 2" in meta[
        "configured_collection_error"
    ]


def test_sessions_close_even_when_a_table_contract_fails() -> None:
    def program(_call: int) -> str:
        return _remove_row(_program_html(), "example8ah", 0)

    site = FixtureSite(program_factory=program)
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert site.sessions and all(current.closed for current in site.sessions)


def test_completed_2026_operation_is_a_valid_no_current_snapshot_in_2027() -> None:
    site = FixtureSite()
    rows, _, meta = _collect(site, today="2027-01-01")
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["current_count"] == 0
    assert meta["detail_attempts"] == meta["detail_pages"] == 0
    assert not any("sub09ak" in url for url in site.calls)


def test_literal_missing_lesson_plan_is_legitimate() -> None:
    rows, _, meta = _collect(FixtureSite())
    assert meta["snapshot_complete"] is True
    child = next(row for row in rows if row["title"] == "어린이 미술교실")
    assert child["raw_fields"]["plan_marker"] == "none"
