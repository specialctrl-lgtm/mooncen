from __future__ import annotations

from collections import Counter
import hashlib
import html
import os
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_jinan as jinan


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FixtureGetter:
    def __init__(
        self,
        pages: Mapping[tuple[str, int], str | list[str]],
        details: Mapping[str, str | list[str]],
    ) -> None:
        self.pages = dict(pages)
        self.details = dict(details)
        self.offsets: Counter[tuple[str, str, int]] = Counter()
        self.calls: list[str] = []

    @staticmethod
    def _value(value: str | list[str], offset: int) -> str:
        if isinstance(value, list):
            return value[min(offset, len(value) - 1)]
        return value

    def __call__(self, _session: Any, url: str, _timeout: int) -> str:
        self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == jinan.JINAN_LIST_PATH:
            category = query["categoryCode1"][0]
            page = int(query["startPage"][0])
            key = (category, page)
            if key not in self.pages:
                raise AssertionError(f"unexpected list request {key}")
            offset_key = ("list", category, page)
            offset = self.offsets[offset_key]
            self.offsets[offset_key] += 1
            return self._value(self.pages[key], offset)
        if parsed.path == jinan.JINAN_DETAIL_PATH:
            identity = query["dataSid"][0]
            if identity not in self.details:
                raise AssertionError(f"unexpected detail request {identity}")
            offset_key = ("detail", identity, 0)
            offset = self.offsets[offset_key]
            self.offsets[offset_key] += 1
            return self._value(self.details[identity], offset)
        raise AssertionError(f"unsafe/unexpected endpoint {url}")


def _target(*, legacy: bool = False, **changes: str) -> dict[str, str]:
    target = {
        "provider": jinan.JINAN_PROVIDER,
        "url": jinan.JINAN_LEGACY_URL if legacy else jinan.JINAN_CANONICAL_URL,
    }
    target.update(changes)
    return target


def _course(
    category: str,
    identity: str,
    *,
    sequence: int = 1,
    title: str | None = None,
    branch_code: str | None = None,
    venue: str = "공식 교육실",
    period: str = "2026-03-01 ~ 2026-11-30",
    apply_period: str = "2026-01-01 09:00 ~ 2026-03-01 18:00",
    capacity_current: int = 0,
    capacity_total: int = 12,
    status: str = "강좌중",
) -> dict[str, Any]:
    branches = jinan.JINAN_BRANCHES[category]
    code = branch_code or (next(iter(branches)) if branches else category)
    center = branches[code] if branches else ""
    return {
        "category": category,
        "identity": identity,
        "sequence": sequence,
        "title": title or f"강좌 {identity}",
        "branch_code": code,
        "center": center,
        "venue": venue,
        "period": period,
        "apply_period": apply_period,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "status": status,
    }


def _tabs() -> str:
    values = []
    for code, name in jinan.JINAN_CATEGORIES.items():
        category2 = next(iter(jinan.JINAN_BRANCHES[code]), "")
        href = (
            "/board/list.jinan?boardId=BBS_0000018&"
            "menuCd=DOM_000000502003000000&"
            f"categoryCode1={code}&categoryCode2={category2}"
        )
        values.append(f'<a href="{html.escape(href)}">{html.escape(name)}</a>')
    return "".join(values)


def _branch_options(category: str) -> str:
    values = ['<option value="">학습센터</option>']
    values.extend(
        f'<option value="{code}">{html.escape(name)}</option>'
        for code, name in jinan.JINAN_BRANCHES[category].items()
    )
    return "".join(values)


def _row(course: Mapping[str, Any], rendered_page: int) -> str:
    detail = urlparse(
        jinan._detail_url(
            str(course["category"]), rendered_page, str(course["identity"])
        )
    )
    href = detail.path + "?" + detail.query
    center = html.escape(str(course["center"]))
    venue = html.escape(str(course["venue"]))
    return f"""
      <tr>
        <td>{course['sequence']}</td>
        <td style="text-align:left"><a href="{html.escape(href)}"
          title="{html.escape(str(course['title']))}">{html.escape(str(course['title']))}</a></td>
        <td style="text-align:left">{center}<br>{venue}</td>
        <td>{course['period']}</td>
        <td>{course['capacity_current']}/{course['capacity_total']}명</td>
        <td>저장 금지 강사</td>
        <td>{course['apply_period']}</td>
        <td>{course['status']}</td>
      </tr>
    """


def _list_page(
    category: str,
    rendered_page: int,
    total: int,
    courses: list[Mapping[str, Any]],
    *,
    branch_name_override: str | None = None,
) -> str:
    last = (total + jinan.JINAN_PAGE_SIZE - 1) // jinan.JINAN_PAGE_SIZE
    options = _branch_options(category)
    if branch_name_override:
        first_name = next(iter(jinan.JINAN_BRANCHES[category].values()))
        options = options.replace(first_name, branch_name_override, 1)
    rows = "".join(_row(course, rendered_page) for course in courses)
    return f"""
      <!doctype html><html lang="ko"><head><meta charset="utf-8">
      <title>개설강좌 목록</title></head><body>
        <nav>{_tabs()}</nav>
        <p class="page">총 게시물 : <span class="count-1">{total}</span>건,
          페이지 : <span class="count-2">{rendered_page}/{last}</span></p>
        <form class="rfc_bbs_searchForm" name="rfc_bbs_searchForm"
          method="get" action="/board/list.jinan">
          <input type="hidden" name="boardId" value="BBS_0000018">
          <input type="hidden" name="menuCd" value="DOM_000000502003000000">
          <input type="hidden" name="contentsSid" value="454">
          <input type="hidden" name="categoryCode1" value="{category}">
          <input type="hidden" name="startPage" value="{rendered_page}">
          <select name="categoryCode2">{options}</select>
          <input type="text" name="keyword" value="">
        </form>
        <table class="basicList"><caption>강좌개설 리스트</caption>
          <thead><tr><th>순번</th><th>강좌명</th>
            <th>학습센터<br>교육장소</th><th>강의기간</th><th>정원</th>
            <th>강사</th><th>신청기간</th><th>진행상태</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </body></html>
    """


def _detail(
    course: Mapping[str, Any],
    *,
    methods: str = "방문,전화,인터넷",
    schedule: str = "매주 화 10:00~12:00",
    target: str = "진안군민",
    fee: str | None = "무료",
    title_override: str | None = None,
    online_control: bool = False,
    control_identity: str | None = None,
) -> str:
    identity = str(course["identity"])
    title = str(title_override or course["title"])
    institution = jinan.JINAN_CATEGORIES[str(course["category"])]
    fee_row = "" if fee is None else f"<th>수강료</th><td>{html.escape(fee)}</td>"
    control = ""
    if online_control:
        bound = control_identity or identity
        write_url = (
            "/board/write.jinan?boardId=BBS_0000019&"
            "menuCd=DOM_000000502003001000&startPage=1&"
            f"studyno={bound}&tNum1={course['capacity_total']}&title1={course['title']}"
        )
        control = f"""
          <script>
            function peopleCountAjax(n){{
              $.ajax({{url:'/index.jinan?contentsSid=372',
                data : {{"up_dataid" : "{bound}"}},
                success:function(data){{if(data == "Y"){{location.href="{write_url}";}}}}
              }});
            }}
          </script>
          <a class="button button-type01" href="#n"
            onclick="peopleCountAjax(); ">강좌신청</a>
        """
    return f"""
      <!doctype html><html lang="ko"><head><meta charset="utf-8">
      <title>개설강좌 상세보기</title></head><body>
        <table class="bbs_list basicWrite"><caption>게시판 리스트</caption><tbody>
          <tr><th>담당부서</th><td>가족행복과</td>
              <th>전화번호</th><td>063-430-2518</td></tr>
          <tr><td colspan="4"></td></tr>
          <tr><th>강좌명</th><td colspan="3">{html.escape(title)}</td></tr>
          <tr><th>기관구분</th><td>{html.escape(institution)}</td>
              <th>학습센터</th><td>{html.escape(str(course['center']))}</td></tr>
          <tr><th>교육대상</th><td>{html.escape(target)}</td>
              <th>교육장소</th><td>{html.escape(str(course['venue']))}</td></tr>
          <tr><th>강좌기간</th><td>{course['period']}</td>
              <th>신청기간</th><td>{course['apply_period']}</td></tr>
          <tr><th>수강시간</th><td>{html.escape(schedule)}</td>{fee_row}</tr>
          <tr><th>강사</th><td>저장 금지 강사</td>
              <th>정원</th><td>{course['capacity_total']}명</td></tr>
          <tr><th>접수방법</th><td>{html.escape(methods)}</td>
              <th>문의전화</th><td>010-1234-5678</td></tr>
          <tr><th>프로그램 설명</th><td colspan="3">저장하면 안 되는 자유 서술</td></tr>
          <tr><th>첨부파일</th><td colspan="3">
            <a href="/private-plan.pdf">강의계획서</a></td></tr>
        </tbody></table>{control}
      </body></html>
    """


def _empty_detail() -> str:
    return """
      <!doctype html><html lang="ko"><head><meta charset="utf-8">
      <title>개설강좌 상세보기</title></head><body><div id="content"></div></body></html>
    """


def _complete_fixture() -> tuple[
    dict[tuple[str, int], str | list[str]],
    dict[str, str | list[str]],
    dict[str, dict[str, Any]],
]:
    courses = {
        "A": _course("A", "1001", title="현재 평생학습"),
        "B": _course(
            "B",
            "203351",
            title="상시 은빛문해",
            status="접수중",
            apply_period="2026-01-05 09:00 ~ 2026-12-31 18:00",
        ),
        "C": _course(
            "C",
            "1003",
            title="만료 유관기관 강좌",
            period="2026-01-01 ~ 2026-07-22",
            status="강좌종료",
        ),
        "D": _course(
            "D",
            "1004",
            title="미래 시민대학",
            period="2026-09-01 ~ 2026-11-30",
            apply_period="2026-04-01 09:00 ~ 2026-08-31 18:00",
            capacity_current=1,
            capacity_total=15,
            status="접수중",
        ),
    }
    pages: dict[tuple[str, int], str | list[str]] = {}
    for category, course in courses.items():
        page = _list_page(category, 1, 1, [course])
        pages[(category, 1)] = page
        # The immediate out-of-range request renders the exact final page.
        pages[(category, 2)] = page
    details: dict[str, str | list[str]] = {
        "1001": _detail(courses["A"]),
        "203351": _empty_detail(),
        "1004": _detail(
            courses["D"],
            methods="방문,인터넷",
            online_control=True,
        ),
    }
    return pages, details, courses


def _collect_fixture(
    pages: Mapping[tuple[str, int], str | list[str]],
    details: Mapping[str, str | list[str]],
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], FixtureGetter, FakeSession]:
    getter = FixtureGetter(pages, details)
    session = FakeSession()
    max_pages = kwargs.pop("max_pages", 10)
    detail_limit = kwargs.pop("detail_limit", 10)
    rows, parser, meta = jinan.collect(
        _target(),
        today="2026-07-23",
        max_pages=max_pages,
        detail_limit=detail_limit,
        session_factory=lambda: session,
        getter=getter,
        **kwargs,
    )
    return rows, parser, meta, getter, session


def test_provider_retarget_hashes_candidates_subsets_and_owner_boundaries() -> None:
    assert jinan.JINAN_PROVIDER == "MUNI_WWW_JINAN_GO_KR_3DF1AE69"
    assert jinan.JINAN_LEGACY_CANDIDATE_ID == "MUNI_IR_6D5FA6516C37"
    assert jinan.JINAN_CANONICAL_CANDIDATE_ID == "MUNI_IR_F003B1D5FD98"
    assert jinan.JINAN_MUNICIPALITY_CODE == "5272000000"
    assert jinan.JINAN_RECOMMENDED_MAX_PAGES == 100
    assert jinan.JINAN_RECOMMENDED_DETAIL_LIMIT == 200
    assert hashlib.sha1(jinan.JINAN_CANONICAL_URL.encode()).hexdigest() == (
        jinan.JINAN_CANONICAL_NORMALIZED_SHA1
    )
    assert hashlib.sha256(jinan.JINAN_CANONICAL_URL.encode()).hexdigest() == (
        jinan.JINAN_CANONICAL_NORMALIZED_SHA256
    )
    assert jinan.is_target(_target())
    assert jinan.is_target(_target(legacy=True))

    canonical = jinan.JINAN_CANDIDATE_AUDIT[jinan.JINAN_CANONICAL_CANDIDATE_ID]
    legacy = jinan.JINAN_CANDIDATE_AUDIT[jinan.JINAN_LEGACY_CANDIDATE_ID]
    assert canonical["provider"] == jinan.JINAN_PROVIDER
    assert canonical["derived_provider_not_used"] == "MUNI_WWW_JINAN_GO_KR_2E2FCB88"
    assert legacy["decision"] == "retain_provider_retarget_static_landing"
    assert jinan.JINAN_DISCOVERY_AUDIT["category_totals"] == {
        "A": 243,
        "B": 589,
        "C": 81,
        "D": 34,
    }
    assert jinan.JINAN_DISCOVERY_AUDIT["category_A_filtered_subsets"] == {
        "one_certificate": 41,
        "education_forum": 4,
    }
    assert jinan.JINAN_KNOWN_REVERSED_EDUCATION_IDS == {"178305", "97144"}
    assert jinan.JINAN_KNOWN_REVERSED_APPLICATION_IDS == {
        "164034",
        "165710",
        "192539",
        "97144",
        "97173",
    }
    assert jinan.JINAN_KNOWN_RANGE_CAPACITY_IDS == {"188071"}
    boundaries = {item["provider"] for item in jinan.JINAN_SEPARATE_OWNER_BOUNDARIES}
    assert "CULTURE_PUBLIC_LIBRARY_FCEB8068F5" in boundaries
    assert "MUNI_WWW_JINAN_GO_KR_F429346A" in boundaries


@pytest.mark.parametrize(
    "changes",
    [
        {"provider": "MUNI_WWW_JINAN_GO_KR_2E2FCB88"},
        {"url": "https://www.jinan.go.kr/edu/index.jinan"},
        {"url": jinan.JINAN_CANONICAL_URL + "&categoryCode1=A"},
        {"url": jinan.JINAN_CANONICAL_URL + "#top"},
        {"url": jinan.JINAN_CANONICAL_URL.replace("https://", "http://")},
        {"url": jinan.JINAN_CANONICAL_URL.replace("www.jinan.go.kr", "www.jinan.go.kr.evil.test")},
        {"url": jinan.JINAN_CANONICAL_URL.replace("boardId=BBS_0000018&menuCd", "menuCd=DOM_000000502003000000&boardId")},
    ],
)
def test_target_matching_is_exact(changes: dict[str, str]) -> None:
    assert not jinan.is_target(_target(**changes))
    rows, parser, meta = jinan.collect(_target(**changes))
    assert rows == []
    assert parser == jinan.JINAN_PARSER
    assert "exact retained/canonical" in meta["configured_collection_error"]


def test_only_exact_list_and_detail_gets_are_allowed() -> None:
    assert jinan._allowed_get_url(jinan._list_url("A", 1))
    assert jinan._allowed_get_url(jinan._detail_url("D", 1, "209024"))
    assert not jinan._allowed_get_url(jinan.JINAN_CANONICAL_URL)
    assert not jinan._allowed_get_url(
        "https://www.jinan.go.kr/index.jinan?contentsSid=372&up_dataid=209024"
    )
    assert not jinan._allowed_get_url(
        "https://www.jinan.go.kr/board/write.jinan?boardId=BBS_0000019&studyno=209024"
    )
    assert not jinan._allowed_get_url(
        "https://lib.jbe.go.kr/jinanplib/index.do"
    )


def test_complete_four_category_clamp_detail_fallback_control_and_pii_contract() -> None:
    pages, details, _courses = _complete_fixture()
    rows, parser, meta, getter, session = _collect_fixture(pages, details)

    assert parser == jinan.JINAN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_requests"] == 19
    assert meta["list_requests"] == 16
    assert meta["detail_pages"] == 3
    assert meta["detail_verified_count"] == 2
    assert meta["detail_fallback_count"] == 1
    assert meta["category_totals"] == {"A": 1, "B": 1, "C": 1, "D": 1}
    assert meta["category_pages"] == {"A": 1, "B": 1, "C": 1, "D": 1}
    assert meta["clamp_pages"] == {"A": 2, "B": 2, "C": 2, "D": 2}
    assert meta["source_rows"] == 4
    assert meta["current_source_count"] == 3
    assert meta["returned_count"] == 3
    assert meta["boundary_rechecks"] == 8
    assert meta["current_list_pages_rechecked"] == {
        "A": [1],
        "B": [1],
        "C": [1],
        "D": [1],
    }
    assert meta["clamp_rechecked"]
    assert meta["pagination_complete"]
    assert meta["details_complete"]
    assert meta["snapshot_complete"]
    assert meta["full_snapshot_validated"]
    assert meta["status_counts"] == {"CLOSED": 1, "OPEN": 2}
    assert meta["application_control_count"] == 1
    assert meta["offline_open_count"] == 1
    assert meta["application_capacity_probes_called"] == 0
    assert meta["application_form_endpoints_called"] == 0
    assert meta["pii_form_endpoints_called"] == 0
    assert session.closed

    by_id = {row["raw_fields"]["identity"]: row for row in rows}
    assert by_id["1001"]["status"] == "CLOSED"
    assert by_id["1004"]["reservation_available"] is True
    assert by_id["1004"]["application_type"] == "ONLINE_RESERVATION"
    assert by_id["1004"]["application_methods"] == ["방문", "온라인"]
    fallback = by_id["203351"]
    assert fallback["status"] == "OPEN"
    assert fallback["reservation_available"] is True
    assert fallback["application_type"] == "INFO_ONLY"
    assert fallback["raw_fields"]["detail_unavailable"] is True
    assert fallback["raw_fields"]["application_control_verified"] is False

    for row in rows:
        assert row["description"] == row["title"]
        assert row["raw_url"] == row["application_url"]
        assert set(row["raw_fields"]) <= jinan._SAFE_RAW_FIELDS
        payload = repr(row)
        assert "063-430-2518" not in payload
        assert "010-1234-5678" not in payload
        assert "저장 금지 강사" not in payload
        assert "private-plan.pdf" not in payload
        assert "저장하면 안 되는 자유 서술" not in payload

    called_paths = {urlparse(url).path for url in getter.calls}
    assert called_paths == {jinan.JINAN_LIST_PATH, jinan.JINAN_DETAIL_PATH}
    assert jinan.JINAN_APPLICATION_CAPACITY_PATH not in called_paths
    assert jinan.JINAN_APPLICATION_WRITE_PATH not in called_paths


def test_branch_directory_drift_fails_atomically() -> None:
    pages, details, _courses = _complete_fixture()
    pages[("A", 1)] = _list_page(
        "A",
        1,
        1,
        [_courses["A"]],
        branch_name_override="임의 신규센터",
    )
    rows, _parser, meta, _getter, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "branch directory drift" in meta["configured_collection_error"]
    assert not meta["snapshot_complete"]


def test_new_nonstandard_historical_period_is_not_silently_accepted() -> None:
    pages, details, courses = _complete_fixture()
    changed = dict(courses["C"], period="20260101 ~ 20260722")
    page = _list_page("C", 1, 1, [changed])
    pages[("C", 1)] = page
    pages[("C", 2)] = page
    rows, _parser, meta, _getter, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "nonstandard education period is not allowlisted" in (
        meta["configured_collection_error"]
    )


def test_immediate_page_must_be_exact_final_page_clamp() -> None:
    pages, details, courses = _complete_fixture()
    changed = dict(courses["A"], title="클램프에서 바뀐 강좌")
    pages[("A", 2)] = _list_page("A", 1, 1, [changed])
    rows, _parser, meta, _getter, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "exact final-page clamp" in meta["configured_collection_error"]


def test_datasid_must_be_globally_unique_across_categories() -> None:
    pages, details, courses = _complete_fixture()
    duplicate = dict(courses["C"], identity="1001")
    page = _list_page("C", 1, 1, [duplicate])
    pages[("C", 1)] = page
    pages[("C", 2)] = page
    rows, _parser, meta, _getter, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "dataSid repeated" in meta["configured_collection_error"]


def test_detail_title_mismatch_fails_atomically() -> None:
    pages, details, courses = _complete_fixture()
    details["1001"] = _detail(courses["A"], title_override="다른 강좌")
    rows, _parser, meta, _getter, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "detail title drift" in meta["configured_collection_error"]


def test_application_script_must_bind_datasid_capacity_and_title() -> None:
    pages, details, courses = _complete_fixture()
    details["1004"] = _detail(
        courses["D"],
        methods="방문,인터넷",
        online_control=True,
        control_identity="9999",
    )
    rows, _parser, meta, _getter, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "application script identity drift" in meta["configured_collection_error"]


def test_unexpected_empty_detail_is_not_silently_fallbacked() -> None:
    pages, details, _courses = _complete_fixture()
    details["1001"] = _empty_detail()
    rows, _parser, meta, _getter, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "not allowlisted" in meta["configured_collection_error"]


def test_max_pages_and_detail_limit_fail_before_partial_save() -> None:
    pages, details, _courses = _complete_fixture()
    getter = FixtureGetter(pages, details)
    session = FakeSession()
    rows, _parser, meta = jinan.collect(
        _target(),
        today="2026-07-23",
        max_pages=1,
        detail_limit=10,
        session_factory=lambda: session,
        getter=getter,
    )
    assert rows == []
    assert "caps are invalid" in meta["configured_collection_error"]
    # Argument validation happens before a network session is allocated.
    assert not session.closed

    rows, _parser, meta, _getter, _session = _collect_fixture(
        pages, details, detail_limit=2
    )
    assert rows == []
    assert "detail_limit 2 below required 3" in meta["configured_collection_error"]
    assert meta["source_cap_reached"]


def test_current_page_mutation_after_detail_fetch_fails_atomically() -> None:
    pages, details, courses = _complete_fixture()
    original = str(pages[("A", 1)])
    changed_course = dict(courses["A"], title="상세 조회 뒤 변경")
    changed = _list_page("A", 1, 1, [changed_course])
    pages[("A", 1)] = [original, changed]
    rows, _parser, meta, _getter, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "stability recheck failed" in meta["configured_collection_error"]
    assert not meta["snapshot_complete"]


def test_custom_dedupe_cannot_drop_a_current_datasid() -> None:
    pages, details, _courses = _complete_fixture()
    rows, _parser, meta, _getter, _session = _collect_fixture(
        pages,
        details,
        dedupe_rows=lambda values: values[:-1],
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.environ.get("RUN_JINAN_LIVE") != "1",
    reason="set RUN_JINAN_LIVE=1 for the audited official-source census",
)
def test_live_exact_four_category_snapshot_2026_07_23() -> None:
    rows, parser, meta = jinan.collect(
        _target(legacy=True),
        today="2026-07-23",
        timeout=30,
        max_pages=jinan.JINAN_RECOMMENDED_MAX_PAGES,
        detail_limit=jinan.JINAN_RECOMMENDED_DETAIL_LIMIT,
    )
    assert parser == jinan.JINAN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["category_totals"] == {"A": 243, "B": 589, "C": 81, "D": 34}
    assert meta["category_pages"] == {"A": 25, "B": 59, "C": 9, "D": 4}
    assert meta["clamp_pages"] == {"A": 26, "B": 60, "C": 10, "D": 5}
    assert meta["source_rows"] == 947
    assert meta["current_source_count"] == 103
    assert meta["returned_count"] == 103
    assert meta["source_status_counts"] == {
        "강좌종료": 784,
        "강좌중": 86,
        "폐강": 62,
        "접수중": 14,
        "준비중": 1,
    }
    assert meta["current_source_status_counts"] == {
        "강좌중": 86,
        "접수중": 14,
        "폐강": 2,
        "준비중": 1,
    }
    assert meta["current_category_counts"] == {"A": 12, "B": 75, "C": 11, "D": 5}
    assert meta["status_counts"] == {
        "CLOSED": 86,
        "OPEN": 14,
        "CANCELLED": 2,
        "SCHEDULED": 1,
    }
    assert meta["detail_pages"] == 103
    assert meta["detail_verified_count"] == 92
    assert meta["detail_fallback_count"] == 11
    assert meta["application_control_count"] == 2
    assert meta["offline_open_count"] == 12
    assert meta["advertised_pages"] == 97
    assert meta["list_requests"] == 126
    assert meta["source_requests"] == 229
    assert meta["boundary_rechecks"] == 25
    assert meta["application_capacity_probes_called"] == 0
    assert meta["application_form_endpoints_called"] == 0
    assert meta["pii_form_endpoints_called"] == 0
    assert meta["pagination_complete"]
    assert meta["details_complete"]
    assert meta["snapshot_complete"]
    assert meta["full_snapshot_validated"]
    assert len({row["provider_course_id"] for row in rows}) == 103
    assert {
        row["raw_fields"]["identity"]
        for row in rows
        if row["raw_fields"]["detail_unavailable"]
    } == jinan.JINAN_KNOWN_EMPTY_DETAIL_IDS
