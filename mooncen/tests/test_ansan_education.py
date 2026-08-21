from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
import json
import os
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_ansan as ansan


@dataclass
class Target:
    provider: str
    url: str
    candidate_id: str = ""


@dataclass(frozen=True)
class Course:
    source_kind: str
    code: str
    identity: str
    title: str
    branch: str
    venue: str
    address: str
    source_status: str = "신청마감"
    start: str = "2026-07-01"
    end: str = "2026-08-31"
    schedule: str = "매주 화요일 10:00 ~ 12:00"
    link_yn: str = "N"
    missing_detail_shell: bool = False


class DummySession:
    def close(self) -> None:
        return None


def _target(**changes: str) -> Target:
    values = {
        "provider": ansan.ANSAN_PROVIDER,
        "url": ansan.ANSAN_CANONICAL_URL,
        "candidate_id": ansan.ANSAN_CANONICAL_CANDIDATE_ID,
    }
    values.update(changes)
    return Target(**values)


def _courses() -> list[Course]:
    result: list[Course] = []
    prefixes = {
        "nor": "NOREDU_",
        "reg": "EDUMNG_",
        "mul": "MULEDU_",
        "road": "ROADMEDU_",
    }
    for index, catalogue in enumerate(ansan.ANSAN_LIFELONG_CATALOGUES, 1):
        venue = "해오름 길거리학습관" if catalogue.code == "road" else "강의실 1"
        result.append(
            Course(
                source_kind="lifelong",
                code=catalogue.code,
                identity=f"{prefixes[catalogue.code]}2026072200{index:02d}",
                title=f"안산 {catalogue.name} 강좌",
                branch=(venue if catalogue.code == "road" else ansan.ANSAN_MAIN_CENTER),
                venue=venue,
                address=(
                    "경기도 안산시 단원구 중앙대로 1"
                    if catalogue.code == "road"
                    else ansan.ANSAN_MAIN_CENTER_ADDRESS
                ),
                source_status="교육접수중" if catalogue.code == "nor" else "교육종료",
            )
        )
    for index, category in enumerate(ansan.ANSAN_RESERVE_CATEGORIES, 1):
        is_legacy_shell = category.code == "E06"
        identity = (
            "2607229001"
            if is_legacy_shell
            else f"RESR_{index:015d}"
        )
        result.append(
            Course(
                source_kind="reserve",
                code=category.code,
                identity=identity,
                title=f"안산 {category.name} 교육 {index}",
                branch=(
                    "단원구 문화센터"
                    if is_legacy_shell
                    else f"안산시 교육기관 {index}"
                ),
                venue=f"교육실 {index}",
                address=(
                    f"경기도 안산시 {'단원구' if index % 2 == 0 else '상록구'} 중앙대로 {index}"
                ),
                source_status="신청가능" if category.code == "E01" else "신청마감",
                link_yn="Y" if is_legacy_shell else "N",
                missing_detail_shell=is_legacy_shell,
            )
        )
    return result


def _lll_directory() -> str:
    links = [
        f'<a href="{escape(item.list_path)}">{escape(item.name)}</a>'
        for item in ansan.ANSAN_LIFELONG_CATALOGUES
    ]
    links.append('<a href="/web/cop/lectEduList.do">강사은행</a>')
    return "".join(links)


def _pair(label: str, value: str, *, label_class: str = "") -> str:
    class_attr = f' class="{label_class}"' if label_class else ""
    return (
        f"<li><strong{class_attr}>{escape(label)}</strong>"
        f'<span class="txt">{escape(value)}</span></li>'
    )


def _lll_card(course: Course) -> str:
    catalogue = next(
        item for item in ansan.ANSAN_LIFELONG_CATALOGUES if item.code == course.code
    )
    control = (
        f'<a class="btn_apply" href="javascript:fn_go_reply(\'{course.identity}\')">수강신청</a>'
        if course.source_status == "교육접수중"
        else ""
    )
    return f"""
      <div class="board_section">
        <div class="cate"><span class="cate_border">{escape(course.source_status)}</span></div>
        <div class="info"><div class="tp">
          <a href="javascript:fn_go_detail('{escape(course.identity)}')">{escape(course.title)}</a>
        </div></div>
        <ul class="bm">
          {_pair('교육기간 :', f'{course.start} ~ {course.end}')}
          {_pair('수강일 :', '화요일')}
          {_pair('시간 :', '10:00 ~ 12:00')}
          {_pair('장소 :', course.venue)}
          {_pair('수강대상자 :', '안산시민')}
        </ul>
        <ul class="edu_status">
          {_pair('신청', '2명', label_class='f')}
          {_pair('정원', '20명', label_class='f')}
        </ul>
        {control}
        <span data-catalogue="{escape(catalogue.code)}"></span>
      </div>
    """


def _lll_list_html(course: Course | None, *, title_suffix: str = "") -> str:
    card = _lll_card(replace(course, title=course.title + title_suffix)) if course else ""
    return f"""
      <html><head><title>안산시평생학습관</title></head><body>
        {_lll_directory()}
        <p>전체 : 1 건</p>
        <div class="list-board">{card}</div>
      </body></html>
    """


def _lll_detail_html(course: Course) -> str:
    control = (
        f'<a href="javascript:fn_go_reply(\'{course.identity}\')">수강신청</a>'
        if course.source_status == "교육접수중"
        else ""
    )
    return f"""
      <html><head><title>{escape(course.title)}</title></head><body>
        <div class="board_section">
          <div class="cate"><span class="cate_border">{escape(course.source_status)}</span></div>
          <div class="info"><div class="tp"><h4>{escape(course.title)}</h4></div></div>
          <ul class="bm">{_pair('교육기간 :', f'{course.start} ~ {course.end}')}</ul>
        </div>
        <section>
          <h4 class="tit">강의 기본정보</h4>
          <div class="board_write">
            <div class="row"><div class="div_th">교육기간</div><div class="div_td">{course.start} ~ {course.end}</div></div>
            <div class="row"><div class="div_th">신청기간</div><div class="div_td">2026-06-01 ~ 2026-07-31</div></div>
            <div class="row"><div class="div_th">교육대상</div><div class="div_td">안산시민</div></div>
            <div class="row"><div class="div_th">강의장</div><div class="div_td">{escape(course.venue)}</div></div>
            <div class="row"><div class="div_th">수강료</div><div class="div_td">무료</div></div>
            <div class="row"><div class="div_th">강사명</div><div class="div_td">저장 금지 강사</div></div>
            <div class="row"><div class="div_th">문의전화</div><div class="div_td">010-0000-0000</div></div>
          </div>
        </section>
        {control}
        <footer>경기도 안산시 상록구 차돌배기로 24-1 / 개인정보 영역</footer>
      </body></html>
    """


def _reserve_options() -> str:
    options = ['<option value="all">전체</option>']
    options.extend(
        f'<option value="{item.code}">{escape(item.name)}</option>'
        for item in ansan.ANSAN_RESERVE_CATEGORIES
    )
    return "".join(options)


def _reserve_card(course: Course) -> str:
    return f"""
      <li>
        <span class="label">{escape(course.source_status)}</span>
        <a onclick="fnView('{escape(course.identity)}','{course.link_yn}')">보기</a>
        <div class="txtW">
          <div class="tit">{escape(course.title)}</div>
          <ul class="etc">
            {_pair('기관/부서', course.branch, label_class='em')}
            {_pair('사용료', '무료', label_class='em')}
            {_pair('교육기간', f'{course.start} ~ {course.end}', label_class='em')}
            {_pair('접수기간', '2026-06-01 ~ 2026-07-31', label_class='em')}
            {_pair('요일', '화', label_class='em')}
            {_pair('교육시간', '10:00 ~ 12:00', label_class='em')}
            {_pair('대상', '안산시민', label_class='em')}
            {_pair('위치', course.venue, label_class='em')}
          </ul>
        </div>
      </li>
    """


def _reserve_list_html(course: Course | None, *, title_suffix: str = "") -> str:
    card = _reserve_card(replace(course, title=course.title + title_suffix)) if course else ""
    return f"""
      <html><head><title>안산시 통합예약시스템</title></head><body>
        <select name="searchClsfCd">{_reserve_options()}</select>
        <p>전체 : 1 건</p>
        <ul class="blog reserv">{card}</ul>
      </body></html>
    """


def _missing_detail_shell() -> str:
    return """
      <html><head><title>안산시 통합예약시스템</title></head><body>
        <main>경기도 안산시 단원구 화랑로 387</main>
        <footer>대표전화 1666-1234</footer>
        <script>alert('존재하지 않는 교육/강좌입니다.');</script>
      </body></html>
    """


def _reserve_detail_html(course: Course) -> str:
    if course.missing_detail_shell:
        return _missing_detail_shell()
    control = (
        '<a id="resvRqstBtn" href="#none" onclick="checkInTracer();">예약신청</a>'
        if course.source_status == "신청가능"
        else ""
    )
    return f"""
      <html><head><title>{escape(course.title)}</title></head><body>
        <div class="listInfo"><div class="infoArea">
          <span class="label">{escape(course.source_status)}</span>
          <div class="tit">{escape(course.title)}</div>
          <button onclick="fnFavorite('{escape(course.identity)}')">관심</button>
          <ul class="itemList">
            {_pair('기관/부서', course.branch, label_class='em')}
            {_pair('교육기간', f'{course.start} ~ {course.end}', label_class='em')}
            {_pair('접수기간', '2026-06-01 ~ 2026-07-31', label_class='em')}
            {_pair('요일', '화', label_class='em')}
            {_pair('교육시간', '10:00 ~ 12:00', label_class='em')}
            {_pair('대상', '안산시민', label_class='em')}
            {_pair('사용료', '무료', label_class='em')}
            {_pair('모집정원', '2명/20명', label_class='em')}
            {_pair('시설명', course.venue, label_class='em')}
          </ul>
          {control}
        </div></div>
        <div class="rsvPlace"><ul class="loca"><li><strong class="em">위치</strong><span>{escape(course.address)}</span></li></ul></div>
        <section>강사 홍길동 / 담당자 010-1111-2222 / 저장 금지 상세 설명</section>
      </body></html>
    """


def _road_place_html(course: Course | None) -> str:
    card = ""
    if course is not None:
        card = f"""
          <div class="board_section board_single map_view">
            <span>No. 1</span>
            <div class="info"><div class="tp"><a>[길거리] {escape(course.venue)}</a></div></div>
            <ul class="bm">{_pair('주소 :', course.address)}</ul>
          </div>
        """
    return f"<html><body>{card}</body></html>"


class HtmlFixture:
    def __init__(self, courses: list[Course] | None = None) -> None:
        self.courses = list(courses or _courses())
        self.pages: dict[str, str] = {}
        self.overrides: dict[tuple[str, int], str] = {}
        self.calls: Counter[str] = Counter()
        self.lock = Lock()

        for catalogue in ansan.ANSAN_LIFELONG_CATALOGUES:
            course = next(
                row
                for row in self.courses
                if row.source_kind == "lifelong" and row.code == catalogue.code
            )
            self.pages[ansan.ansan_lifelong_list_url(catalogue)] = _lll_list_html(course)
            self.pages[ansan.ansan_lifelong_list_url(catalogue, 2)] = _lll_list_html(None)
            self.pages[ansan.ansan_lifelong_detail_url(catalogue, course.identity)] = (
                _lll_detail_html(course)
            )

        for category in ansan.ANSAN_RESERVE_CATEGORIES:
            course = next(
                row
                for row in self.courses
                if row.source_kind == "reserve" and row.code == category.code
            )
            self.pages[ansan.ansan_reserve_list_url(category)] = _reserve_list_html(course)
            self.pages[ansan.ansan_reserve_list_url(category, 2)] = _reserve_list_html(None)
            self.pages[
                ansan.ansan_reserve_detail_url(category, course.identity, course.link_yn)
            ] = _reserve_detail_html(course)

        road_course = next(
            row
            for row in self.courses
            if row.source_kind == "lifelong" and row.code == "road"
        )
        self.pages[ansan.ansan_road_place_list_url()] = _road_place_html(road_course)
        self.pages[ansan.ansan_road_place_list_url(2)] = _road_place_html(None)

    def fetch(self, _session: DummySession, url: str, _timeout: int) -> str:
        with self.lock:
            self.calls[url] += 1
            call = self.calls[url]
        overridden = self.overrides.get((url, call))
        if overridden is not None:
            return overridden
        if url not in self.pages:
            raise RuntimeError(f"unexpected URL: {url}")
        return self.pages[url]


def _collect(fixture: HtmlFixture, **kwargs):
    options = {
        "today": "2026-07-22",
        "timeout": 5,
        "max_pages": 100,
        "detail_limit": 100,
        "max_workers": 4,
        "session_factory": DummySession,
        "fetcher": fixture.fetch,
    }
    options.update(kwargs)
    return ansan.collect_ansan_education_courses(_target(), **options)


def test_constants_urls_exact_target_and_candidate_ownership() -> None:
    assert ansan.ANSAN_PROVIDER == "MUNI_LLL_ANSAN_GO_KR_691646BE"
    assert ansan.ANSAN_CANONICAL_CANDIDATE_ID == "MUNI_IR_348E08281517"
    assert ansan.ANSAN_CITY_CODE == "4127000000"
    assert ansan.ANSAN_CANONICAL_URL.endswith("/web/cop/norEduList.do")
    assert ansan.is_target(_target())
    assert not ansan.is_target(_target(provider="WRONG"))
    assert not ansan.is_target(_target(url=ansan.ANSAN_CANONICAL_URL + "?pageIndex=1"))
    assert not ansan.is_target(_target(url="http://lll.ansan.go.kr/web/cop/norEduList.do"))

    assert ansan.ANSAN_CANDIDATE_AUDIT["MUNI_IR_201E1EBB44E3"]["decision"] == (
        "lifelong_catalogue_subset"
    )
    assert ansan.ANSAN_CANDIDATE_AUDIT["MUNI_IR_C4AD132627A7"]["decision"] == (
        "reservation_navigation_shell"
    )
    assert ansan.ANSAN_CANDIDATE_AUDIT["MUNI_IR_452897AE0425"]["decision"] == (
        "excluded_wrong_category_experience"
    )
    assert {
        item.url: item.ownership for item in ansan.ANSAN_NON_EXECUTING_ALIASES
    }["https://lll.ansan.go.kr/web/cop/regEduList.do"] == (
        "lifelong_catalogue_subset"
    )


def test_url_builders_preserve_exact_partitions_and_identity_contracts() -> None:
    reg = next(item for item in ansan.ANSAN_LIFELONG_CATALOGUES if item.code == "reg")
    assert parse_qs(urlparse(ansan.ansan_lifelong_list_url(reg, 7)).query) == {
        "pageUnit": ["100"],
        "pageIndex": ["7"],
    }
    assert parse_qs(
        urlparse(ansan.ansan_lifelong_detail_url(reg, "EDUMNG_202607220001")).query
    ) == {"mId": ["EDUMNG_202607220001"]}
    science = next(item for item in ansan.ANSAN_RESERVE_CATEGORIES if item.code == "E07")
    assert parse_qs(urlparse(ansan.ansan_reserve_list_url(science, 9)).query) == {
        "currentMenuNo": ["703"],
        "pageIndex": ["9"],
    }
    assert parse_qs(
        urlparse(ansan.ansan_reserve_detail_url(science, "2607229001", "Y")).query
    ) == {
        "currentMenuNo": ["703"],
        "resrId": ["2607229001"],
        "linkYn": ["Y"],
    }
    with pytest.raises(ValueError):
        ansan.ansan_reserve_detail_url(science, "2607229001", "N")


def test_complete_snapshot_covers_both_systems_and_non_open_legacy_shell() -> None:
    fixture = HtmlFixture()
    rows, parser, meta = _collect(fixture)

    assert parser == ansan.ANSAN_PARSER
    assert len(rows) == 11
    assert meta["source_total"] == 11
    assert meta["lifelong_total"] == 4
    assert meta["reserve_total"] == 7
    assert meta["road_place_total"] == 1
    assert meta["required_list_requests"] == 36
    assert meta["pages"] == 36
    assert meta["data_pages"] == 12
    assert meta["sentinel_requests"] == 12
    assert meta["stability_rechecks"] == 12
    assert meta["current_count"] == 11
    assert meta["expired_count"] == 0
    assert meta["detail_pages"] == 11
    assert meta["detail_errors"] == 0
    assert meta["closed_detail_retired_count"] == 1
    assert meta["scheduled_detail_unpublished_count"] == 0
    assert meta["non_open_detail_shell_count"] == 1
    assert meta["application_control_count"] == 2
    assert meta["pagination_complete"] is True
    assert meta["partitions_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert Counter(row["raw_fields"]["source_kind"] for row in rows) == {
        "lifelong": 4,
        "reserve": 7,
    }
    assert Counter(row["status"] for row in rows) == {"OPEN": 2, "CLOSED": 9}
    assert sum(meta["branch_counts"].values()) == 11
    assert sum(meta["municipality_counts"].values()) == 11

    legacy = next(
        row
        for row in rows
        if row["raw_fields"]["source_identity"] == "2607229001"
    )
    assert legacy["status"] == "CLOSED"
    assert legacy["application_url"] == ""
    assert legacy["application_type"] == "INFORMATION_ONLY"
    assert legacy["municipality_code"] == ansan.ANSAN_DANWON_CODE
    assert legacy["raw_fields"]["status_control_override"] == (
        "closed_legacy_detail_retired"
    )


def test_open_row_never_accepts_missing_legacy_detail_shell() -> None:
    courses = _courses()
    index = next(
        index
        for index, row in enumerate(courses)
        if row.source_kind == "reserve" and row.code == "E06"
    )
    courses[index] = replace(courses[index], source_status="신청가능")
    fixture = HtmlFixture(courses)

    rows, _parser, meta = _collect(fixture)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["detail_errors"] == 1
    assert "missing reservation detail info" in meta["configured_collection_error"]


def test_scheduled_legacy_missing_detail_remains_information_only() -> None:
    courses = _courses()
    index = next(
        index
        for index, row in enumerate(courses)
        if row.source_kind == "reserve" and row.code == "E06"
    )
    courses[index] = replace(courses[index], source_status="접수대기")
    rows, _parser, meta = _collect(HtmlFixture(courses))

    assert meta["snapshot_complete"] is True
    assert meta["scheduled_detail_unpublished_count"] == 1
    assert meta["closed_detail_retired_count"] == 0
    scheduled = next(
        row
        for row in rows
        if row["raw_fields"]["source_identity"] == "2607229001"
    )
    assert scheduled["status"] == "SCHEDULED"
    assert scheduled["reservation_available"] is False
    assert scheduled["raw_fields"]["status_control_override"] == (
        "scheduled_legacy_detail_not_yet_published"
    )


def test_page_and_detail_caps_fail_closed_before_partial_snapshot() -> None:
    fixture = HtmlFixture()
    rows, _parser, meta = _collect(fixture, max_pages=35)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["required_list_requests"] == 36
    assert "max_pages 35 < required 36" in meta["configured_collection_error"]

    fixture = HtmlFixture()
    rows, _parser, meta = _collect(fixture, detail_limit=10)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["current_count"] == 11
    assert "detail_limit 10 < current rows 11" in meta["configured_collection_error"]


def test_nonempty_sentinel_and_page_one_drift_fail_closed() -> None:
    fixture = HtmlFixture()
    category = ansan.ANSAN_RESERVE_CATEGORIES[0]
    course = next(
        row
        for row in fixture.courses
        if row.source_kind == "reserve" and row.code == category.code
    )
    fixture.pages[ansan.ansan_reserve_list_url(category, 2)] = _reserve_list_html(course)
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "reserve E01 sentinel is not empty" in meta["configured_collection_error"]

    fixture = HtmlFixture()
    catalogue = ansan.ANSAN_LIFELONG_CATALOGUES[0]
    course = next(
        row
        for row in fixture.courses
        if row.source_kind == "lifelong" and row.code == catalogue.code
    )
    fixture.overrides[(ansan.ansan_lifelong_list_url(catalogue), 2)] = (
        _lll_list_html(course, title_suffix=" 변경")
    )
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "lifelong nor page-one drift" in meta["configured_collection_error"]


def test_rows_keep_only_allowlisted_structured_evidence() -> None:
    rows, _parser, meta = _collect(HtmlFixture())
    assert meta["snapshot_complete"] is True
    forbidden = {
        "강사",
        "강사명",
        "문의",
        "문의전화",
        "전화번호",
        "이메일",
        "description",
        "source_html",
        "attachment",
    }
    for row in rows:
        assert not (set(row) & forbidden)
        assert set(row["raw_fields"]) <= ansan.ANSAN_RAW_FIELD_ALLOWLIST
        assert "저장 금지" not in repr(row)
        assert "010-" not in repr(row)


def test_invalid_target_and_missing_tls_session_fail_without_network() -> None:
    rows, parser, meta = ansan.collect_ansan_education_courses(
        _target(provider="WRONG")
    )
    assert rows == []
    assert parser == ansan.ANSAN_PARSER
    assert "not the canonical" in meta["configured_collection_error"]

    rows, _parser, meta = ansan.collect_ansan_education_courses(_target())
    assert rows == []
    assert "legacy-TLS session_factory injection is required" in (
        meta["configured_collection_error"]
    )


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_CRAWLER_TESTS") != "1",
    reason="set RUN_LIVE_CRAWLER_TESTS=1 for the complete official Ansan audit",
)
def test_live_complete_ansan_snapshot() -> None:
    rows, parser, meta = ansan.collect_ansan_education_courses(
        _target(),
        timeout=35,
        max_pages=1200,
        detail_limit=3000,
        max_workers=ansan.ANSAN_MAX_WORKERS,
        allow_raw_requests_for_tests=True,
    )
    assert parser == ansan.ANSAN_PARSER
    assert meta["source_total"] == meta["lifelong_total"] + meta["reserve_total"]
    assert meta["source_total"] > 10_000
    assert meta["road_place_total"] > 0
    assert meta["current_count"] > 0
    assert meta["detail_pages"] == meta["current_count"]
    assert meta["returned_count"] == len(rows)
    assert meta["pagination_complete"] is True
    assert meta["partitions_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert not meta["source_cap_reached"]
    assert sum(meta["branch_counts"].values()) == len(rows)
    assert sum(meta["municipality_counts"].values()) == len(rows)
    assert set(meta["municipality_counts"]) <= set(
        ansan.ANSAN_MUNICIPALITY_NAMES.values()
    )
    print(
        "ANSAN_LIVE_META="
        + json.dumps(
            {
                "rows": len(rows),
                "parser": parser,
                "pages": meta["pages"],
                "required_list_requests": meta["required_list_requests"],
                "data_pages": meta["data_pages"],
                "sentinel_requests": meta["sentinel_requests"],
                "stability_rechecks": meta["stability_rechecks"],
                "source_total": meta["source_total"],
                "lifelong_total": meta["lifelong_total"],
                "reserve_total": meta["reserve_total"],
                "road_place_total": meta["road_place_total"],
                "lifelong_catalogue_totals": meta["lifelong_catalogue_totals"],
                "reserve_category_totals": meta["reserve_category_totals"],
                "source_status_counts": meta["source_status_counts"],
                "current_count": meta["current_count"],
                "expired_count": meta["expired_count"],
                "detail_pages": meta["detail_pages"],
                "detail_retry_pages": meta["detail_retry_pages"],
                "scheduled_detail_unpublished_count": meta[
                    "scheduled_detail_unpublished_count"
                ],
                "closed_detail_retired_count": meta[
                    "closed_detail_retired_count"
                ],
                "application_control_count": meta[
                    "application_control_count"
                ],
                "cross_source_overlap_count": meta[
                    "cross_source_overlap_count"
                ],
                "municipality_counts": meta["municipality_counts"],
                "branch_count": len(meta["branch_counts"]),
                "branch_counts": meta["branch_counts"],
                "normalized_status_counts": dict(
                    sorted(Counter(row["status"] for row in rows).items())
                ),
                "source_kind_counts": dict(
                    sorted(
                        Counter(
                            row["raw_fields"]["source_kind"] for row in rows
                        ).items()
                    )
                ),
                "snapshot_complete": meta["snapshot_complete"],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
