from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from html import escape
import hashlib
import json
import os
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_yeongju as yeongju


@dataclass
class Target:
    provider: str = yeongju.YEONGJU_PROVIDER
    url: str = yeongju.YEONGJU_CANONICAL_URL


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    year: int
    apply_start: str
    apply_end: str
    start: str
    end: str
    status: str
    capacity: int
    current: int
    venue: str = "영주시 평생학습센터 101호"
    hours: str = "10:00~12:00"
    fee: str = "무료"
    materials: str = "없음"


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class DummyResponse:
    def __init__(self, url: str, html: str) -> None:
        self.url = url
        self.status_code = 200
        self.content = html.encode("utf-8")
        self.headers = {"Content-Type": "text/html; charset=UTF-8"}


def _courses() -> dict[str, Course]:
    return {
        "1300": Course(
            "1300",
            "시민교육과 원데이에 함께 노출된 강좌",
            2026,
            "07/01 09시",
            "07/31 18시",
            "2026.08.01",
            "2026.08.31",
            "접수중",
            15,
            5,
        ),
        "1299": Course(
            "1299",
            "종료된 시민교육",
            2026,
            "05/01 09시",
            "05/10 18시",
            "2026.05.15",
            "2026.06.01",
            "접수마감",
            20,
            20,
        ),
        "1298": Course(
            "1298",
            "교육 중인 마감 시민교육",
            2026,
            "06/01 09시",
            "06/10 18시",
            "2026.06.15",
            "2026.07.30",
            "접수마감",
            12,
            12,
            venue="영주시 평생학습센터 202호",
        ),
        "1400": Course(
            "1400",
            "가을 원데이클래스",
            2026,
            "08/01 09시",
            "08/10 18시",
            "2026.09.01",
            "2026.09.01",
            "접수예정",
            15,
            0,
        ),
        "1500": Course(
            "1500",
            "신중년 여름학교",
            2026,
            "06/01 09시",
            "06/30 18시",
            "2026.07.01",
            "2026.07.23",
            "접수종료",
            30,
            27,
            venue="영주시 평생학습센터 글빛마루",
        ),
        "1600": Course(
            "1600",
            "평생학습 공개특강",
            2026,
            "07/20 09시",
            "07/25 18시",
            "2026.07.26",
            "2026.07.26",
            "접수중",
            50,
            11,
            venue="영주시 평생학습센터 203호",
        ),
        "1700": Course(
            "1700",
            "지난 행복학습센터 강좌",
            2026,
            "04/01 09시",
            "04/10 18시",
            "2026.04.15",
            "2026.05.15",
            "마감",
            10,
            10,
        ),
    }


def _catalogue_rows() -> dict[str, dict[int, list[Course]]]:
    courses = _courses()
    return {
        "regular": {1: [courses["1300"], courses["1299"]], 2: [courses["1298"]]},
        "oneday": {1: [courses["1300"], courses["1400"]]},
        "chungchun": {1: [courses["1500"]]},
        "special": {1: [courses["1600"]]},
        "happy": {1: [courses["1700"]]},
        "activist": {1: []},
    }


def _source(code: str) -> yeongju.YeongjuCatalogue:
    return next(item for item in yeongju.YEONGJU_CATALOGUES if item.code == code)


def _detail_href(course: Course, page: int) -> str:
    return (
        f"lecture_detail.php?seq={course.identity}&page={page}&stype=&stext="
        f"&syear={course.year}&sclass=1&"
    )


def _list_row(course: Course, page: int, number: int) -> str:
    return f"""
      <tr>
        <td>{number}</td>
        <td>{course.year}</td>
        <td>
          <a href="{escape(_detail_href(course, page), quote=True)}">
            <span>{escape(course.title)}</span>
          </a><br>
          {course.apply_start} ~ {course.apply_end}<br>
        </td>
        <td>{course.capacity}명</td>
        <td>{course.current}명</td>
        <td>{course.start}. ~ {course.end}. 월요일 / {course.hours}</td>
        <td>{escape(course.venue)} / {escape(course.materials)}</td>
        <td><a href="lecture_detail.php?seq={course.identity}">{course.status}</a></td>
      </tr>
    """


def _list_html(
    source: yeongju.YeongjuCatalogue,
    courses: list[Course],
    page: int,
    declared_pages: int,
    *,
    empty: bool = False,
    title_suffix: str = "",
) -> str:
    if empty or not courses:
        body = '<tr><td colspan="9">접수가능한 강좌 또는검색결과가 없습니다.</td></tr>'
    else:
        body = "".join(
            _list_row(course, page, number)
            for number, course in enumerate(courses, start=1)
        )
    pager = "".join(
        f'<a class="page" href="{source.path}?page={number}">{number}</a>'
        for number in range(1, declared_pages + 1)
    )
    return f"""<!doctype html><html><head>
      <title>영주시 평생학습센터{escape(title_suffix)}</title></head><body>
      <table class="table_black" summary="검색 조건에 따른 수강과목 리스트 정보를 제공합니다.">
        <caption>수강과목 목록</caption>
        <thead><tr>
          <th>번호</th><th>모집년도</th><th>과목명/접수기간</th>
          <th>온라인 모집</th><th>신청인원</th><th>교육기간/강의시간</th>
          <th>장소/금액</th><th>접수</th>
        </tr></thead><tbody>{body}</tbody>
      </table><div class="paging">{pager}</div>
      <div id="agree-front" data-private="true">PRIVATE_PRIVACY_OVERLAY 010-5555-9999</div>
      </body></html>"""


def _detail_html(
    course: Course,
    *,
    unknown_field: bool = False,
    title_override: str = "",
    include_control: bool | None = None,
) -> str:
    if include_control is None:
        include_control = course.status == "접수중"
    control = ""
    if include_control:
        control = """
          <a href="https://www.yeongjulll.go.kr/main.jsp?home_url=yeongjulll&amp;code=MEMBER_LOGIN">
            <button type="button" class="bt_fn type_a" target="접수하기">접수하기</button>
          </a>
        """
    unknown = (
        "<tr><th>새 개인정보</th><td>PRIVATE_UNKNOWN 010-7777-8888</td></tr>"
        if unknown_field
        else ""
    )
    return f"""<!doctype html><html><head><title>영주시 평생학습센터</title></head><body>
      <div id="lecture_cnt">
        <div class="detail_name">{escape(title_override or course.title)}</div>
        <table class="table_basic"><caption>과목정보 목록</caption><tbody>
          <tr><th>모집년도</th><td>{course.year}</td></tr>
          <tr><th>모집인원</th><td>{course.capacity}명 (후보인원: 3명)</td>
              <th>신청인원</th><td>{course.current}명</td></tr>
          <tr><th>접수기간</th><td>{course.year}-{course.apply_start[:5].replace('/', '-')} ~ {course.year}-{course.apply_end[:5].replace('/', '-')}</td>
              <th>결제금액</th><td>{escape(course.fee)}</td></tr>
          <tr><th>교육기간</th><td>{course.start}. ~ {course.end}. 월요일 / {course.hours}</td></tr>
          <tr><th>교육장소</th><td>{escape(course.venue)}</td>
              <th>강사명</th><td>PRIVATE_INSTRUCTOR instructor@example.com</td></tr>
          <tr><th>교육내용</th><td>PRIVATE_EDUCATION_TEXT 010-1234-5678</td></tr>
          <tr><th>재료비</th><td>{escape(course.materials)}</td>
              <th>기타사항</th><td>PRIVATE_REMARK applicant@example.com</td></tr>
          {unknown}
        </tbody></table>
        <div class="textr">{control}<a href="lecture_list.php"><button target="강좌목록">강좌목록</button></a></div>
      </div>
      <form id="private-applicant"><input name="phone" value="010-9999-1111"></form>
      </body></html>"""


class FixtureFetcher:
    def __init__(
        self,
        rows: dict[str, dict[int, list[Course]]] | None = None,
        *,
        drift: tuple[str, int] | None = None,
        bad_sentinel: str = "",
        conflict_duplicate: bool = False,
        unknown_detail: str = "",
        status_contradiction: str = "",
    ) -> None:
        self.rows = rows if rows is not None else _catalogue_rows()
        self.drift = drift
        self.bad_sentinel = bad_sentinel
        self.conflict_duplicate = conflict_duplicate
        self.unknown_detail = unknown_detail
        self.status_contradiction = status_contradiction
        self.calls: list[str] = []
        self.counts: dict[str, int] = {}
        self.details = _courses()

    def __call__(self, _session: DummySession, url: str, _timeout: int) -> DummyResponse:
        self.calls.append(url)
        self.counts[url] = self.counts.get(url, 0) + 1
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == yeongju.YEONGJU_DETAIL_PATH:
            identity = query["seq"][0]
            course = self.details[identity]
            return DummyResponse(
                url,
                _detail_html(
                    course,
                    unknown_field=identity == self.unknown_detail,
                ),
            )
        source = next(item for item in yeongju.YEONGJU_CATALOGUES if item.path == parsed.path)
        pages = self.rows[source.code]
        declared = max(pages, default=1)
        if query.get("sclass") == [yeongju.YEONGJU_EMPTY_SENTINEL_CLASS]:
            if source.code == self.bad_sentinel:
                return DummyResponse(
                    url,
                    _list_html(source, [self.details["1600"]], 1, 1),
                )
            return DummyResponse(url, _list_html(source, [], 1, 1, empty=True))
        page = int(query.get("page", ["1"])[0])
        items = list(pages.get(page, []))
        if source.code == "oneday" and self.conflict_duplicate:
            items = [replace(item, title="충돌하는 중복 제목") if item.identity == "1300" else item for item in items]
        if self.status_contradiction:
            items = [
                replace(item, status="접수중", apply_start="08/01 09시", apply_end="08/10 18시")
                if item.identity == self.status_contradiction
                else item
                for item in items
            ]
        suffix = ""
        if self.drift == (source.code, page) and self.counts[url] >= 2:
            suffix = " - 바뀐 소유자"
        return DummyResponse(
            url,
            _list_html(source, items, page, declared, empty=not items, title_suffix=suffix),
        )


def _collect(fetcher: FixtureFetcher, **kwargs: object):
    sessions: list[DummySession] = []

    def factory() -> DummySession:
        current = DummySession()
        sessions.append(current)
        return current

    result = yeongju.collect_yeongju_education(
        Target(),
        cutoff=date(2026, 7, 22),
        session_factory=factory,
        html_fetcher=fetcher,
        sleeper=lambda _seconds: None,
        **kwargs,
    )
    assert sessions and all(item.closed for item in sessions)
    return result


def test_canonical_identity_and_six_catalogue_scope() -> None:
    digest = hashlib.sha1(yeongju.YEONGJU_CANONICAL_URL.encode("utf-8")).hexdigest()[:8].upper()
    assert yeongju.YEONGJU_PROVIDER == f"MUNI_WWW_YEONGJULLL_GO_KR_{digest}"
    assert [item.code for item in yeongju.YEONGJU_CATALOGUES] == [
        "regular",
        "oneday",
        "chungchun",
        "special",
        "happy",
        "activist",
    ]
    assert len({item.url for item in yeongju.YEONGJU_CATALOGUES}) == 6
    assert yeongju.YEONGJU_CATALOGUES[0].url == yeongju.YEONGJU_CANONICAL_URL


def test_guide_and_old_candidate_are_aliases_not_owners() -> None:
    guide = yeongju.YEONGJU_OWNER_BOUNDARY_AUDIT[yeongju.YEONGJU_GUIDE_PROVIDER]
    oneday = yeongju.YEONGJU_OWNER_BOUNDARY_AUDIT[yeongju.YEONGJU_ONEDAY_PROVIDER]
    root = yeongju.YEONGJU_OWNER_BOUNDARY_AUDIT[yeongju.YEONGJU_ROOT_PROVIDER]
    assert guide["decision"] == "exclude_information_page_alias_not_a_course_ledger"
    assert oneday["decision"] == "disable_separate_schedule_included_under_canonical_owner"
    assert root["decision"] == "exclude_navigation_and_five-item_highlight_alias"
    assert guide["canonical_owner"] == oneday["canonical_owner"] == yeongju.YEONGJU_PROVIDER


def test_target_matching_is_exact_and_does_not_claim_aliases() -> None:
    assert yeongju.is_yeongju_education_target(Target())
    assert not yeongju.is_yeongju_education_target(
        Target(provider=yeongju.YEONGJU_GUIDE_PROVIDER, url=yeongju.YEONGJU_GUIDE_URL)
    )
    assert not yeongju.is_yeongju_education_target(
        Target(provider=yeongju.YEONGJU_ONEDAY_PROVIDER, url=yeongju.YEONGJU_ONEDAY_URL)
    )
    assert not yeongju.is_yeongju_education_target(
        Target(url=yeongju.YEONGJU_CANONICAL_URL + "?page=1")
    )


def test_url_builders_are_bounded_to_the_official_host() -> None:
    assert yeongju.yeongju_catalogue_url("regular", 1) == yeongju.YEONGJU_CANONICAL_URL
    assert yeongju.yeongju_catalogue_url("regular", 2).endswith("lecture_list.php?page=2")
    assert yeongju.yeongju_sentinel_url("special").endswith(
        "lecture_list_specialprogram.php?sclass=999999"
    )
    assert yeongju.yeongju_detail_url("1290").endswith("lecture_detail.php?seq=1290")
    with pytest.raises(yeongju.YeongjuContractError):
        yeongju.yeongju_detail_url("../../private")


def test_complete_six_catalogue_snapshot_dedupes_and_filters() -> None:
    fetcher = FixtureFetcher()
    rows, parser, meta = _collect(fetcher)

    assert parser == yeongju.YEONGJU_PARSER
    assert [row["provider_course_id"] for row in rows] == [
        "yeongju_lifelong:1300",
        "yeongju_lifelong:1298",
        "yeongju_lifelong:1400",
        "yeongju_lifelong:1500",
        "yeongju_lifelong:1600",
    ]
    assert [row["status"] for row in rows] == ["OPEN", "CLOSED", "SCHEDULED", "CLOSED", "OPEN"]
    assert all(row["branch"] == yeongju.YEONGJU_BRANCH for row in rows)
    assert all(row["reservation_available"] is False for row in rows)
    assert all("application_url" not in row for row in rows)
    assert rows[0]["raw_fields"]["source_catalogues"] == ("regular", "oneday")

    assert meta["catalogue_source_counts"] == {
        "regular": 3,
        "oneday": 2,
        "chungchun": 1,
        "special": 1,
        "happy": 1,
        "activist": 0,
    }
    assert meta["catalogue_current_counts"] == {
        "regular": 2,
        "oneday": 2,
        "chungchun": 1,
        "special": 1,
        "happy": 0,
        "activist": 0,
    }
    assert meta["catalogue_pages"] == {
        "regular": 2,
        "oneday": 1,
        "chungchun": 1,
        "special": 1,
        "happy": 1,
        "activist": 1,
    }
    assert meta["source_rows"] == 8
    assert meta["unique_source_rows"] == 7
    assert meta["cross_catalogue_duplicates"] == 1
    assert meta["current_source_count"] == 5
    assert meta["expired_source_count"] == 2
    assert meta["detail_pages"] == 5
    assert meta["list_requests"] == 31
    assert meta["empty_sentinel_requests"] == 12
    assert meta["stability_rechecks"] == 18
    assert meta["source_status_counts"] == {
        "접수중": 3,
        "접수마감": 2,
        "접수예정": 1,
        "접수종료": 1,
        "마감": 1,
    }
    assert meta["current_status_counts"] == {"OPEN": 2, "CLOSED": 2, "SCHEDULED": 1}
    assert meta["identityless_login_gates_excluded"] == 2
    assert meta["branch_counts"] == {yeongju.YEONGJU_BRANCH: 5}
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["no_current_data"] is False
    assert meta["application_form_requests"] == meta["login_page_requests"] == 0

    requested_paths = [urlparse(url).path for url in fetcher.calls]
    assert yeongju.YEONGJU_LOGIN_PATH not in requested_paths
    assert "/lecture/mypage.php" not in requested_paths
    assert yeongju.yeongju_detail_url("1299") not in fetcher.calls
    assert yeongju.yeongju_detail_url("1700") not in fetcher.calls


def test_private_detail_values_are_never_emitted() -> None:
    fetcher = FixtureFetcher()
    rows, _parser, meta = _collect(fetcher)
    serialized = json.dumps({"rows": rows, "meta": meta}, ensure_ascii=False, default=str)
    for forbidden in (
        "PRIVATE_INSTRUCTOR",
        "PRIVATE_EDUCATION_TEXT",
        "PRIVATE_REMARK",
        "PRIVATE_PRIVACY_OVERLAY",
        "010-1234-5678",
        "instructor@example.com",
        "applicant@example.com",
    ):
        assert forbidden not in serialized
    assert "instructor" not in rows[0]
    assert "phone" not in rows[0]
    assert "contact" not in rows[0]


def test_all_six_official_empty_rows_are_stable_no_current_data() -> None:
    empty = {source.code: {1: []} for source in yeongju.YEONGJU_CATALOGUES}
    fetcher = FixtureFetcher(empty)
    rows, parser, meta = _collect(fetcher)
    assert rows == []
    assert parser == yeongju.YEONGJU_PARSER
    assert meta["catalogue_source_counts"] == {source.code: 0 for source in yeongju.YEONGJU_CATALOGUES}
    assert meta["source_rows"] == meta["current_source_count"] == meta["detail_pages"] == 0
    assert meta["list_requests"] == 30
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["configured_collection_error"] == ""


def test_conflicting_identity_across_catalogues_fails_closed() -> None:
    rows, _parser, meta = _collect(FixtureFetcher(conflict_duplicate=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sibling catalogue facts conflict" in meta["configured_collection_error"]


def test_nonempty_structural_sentinel_fails_closed() -> None:
    rows, _parser, meta = _collect(FixtureFetcher(bad_sentinel="special"))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "empty sentinel stability failed" in meta["configured_collection_error"]


def test_first_or_last_boundary_drift_fails_closed() -> None:
    rows, _parser, meta = _collect(FixtureFetcher(drift=("regular", 2)))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "document owner/title" in meta["configured_collection_error"]


def test_detail_limit_never_returns_a_partial_snapshot() -> None:
    fetcher = FixtureFetcher()
    rows, _parser, meta = _collect(fetcher, detail_limit=4)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["detail_pages"] == 0
    assert "detail_limit" in meta["configured_collection_error"]


def test_unknown_detail_field_fails_before_private_value_can_escape() -> None:
    rows, _parser, meta = _collect(FixtureFetcher(unknown_detail="1300"))
    assert rows == []
    assert "unexpected field '새 개인정보'" in meta["configured_collection_error"]
    assert "PRIVATE_UNKNOWN" not in json.dumps(meta, ensure_ascii=False, default=str)


def test_open_status_must_agree_with_application_dates() -> None:
    rows, _parser, meta = _collect(FixtureFetcher(status_contradiction="1300"))
    assert rows == []
    assert "OPEN status contradicts reception dates" in meta["configured_collection_error"]


def test_discovery_audit_records_live_sibling_counts() -> None:
    audit = yeongju.YEONGJU_DISCOVERY_AUDIT
    assert audit["checked_on"] == "2026-07-22"
    assert audit["catalogue_source_rows"] == {
        "regular": 0,
        "oneday": 0,
        "chungchun": 0,
        "special": 1,
        "happy": 0,
        "activist": 0,
    }
    assert audit["current_identities"] == ("1290",)
    assert audit["current_branch_counts"] == {yeongju.YEONGJU_BRANCH: 1}
    assert audit["guide_page_is_catalogue"] is False


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_YEONGJU_EDUCATION") != "1",
    reason="set RUN_LIVE_YEONGJU_EDUCATION=1 for the official live contract test",
)
def test_live_yeongju_six_catalogue_contract() -> None:
    rows, parser, meta = yeongju.collect_yeongju_education(
        Target(),
        timeout=30,
        max_pages=20,
        detail_limit=200,
    )
    assert parser == yeongju.YEONGJU_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert set(meta["catalogue_source_counts"]) == {
        source.code for source in yeongju.YEONGJU_CATALOGUES
    }
    assert meta["source_rows"] >= meta["unique_source_rows"] >= len(rows)
    assert meta["detail_pages"] == meta["current_source_count"] == len(rows)
    assert meta["application_form_requests"] == meta["login_page_requests"] == 0
    today = date.today()
    for row in rows:
        assert row["provider"] == yeongju.YEONGJU_PROVIDER
        assert row["branch"] == yeongju.YEONGJU_BRANCH
        assert date.fromisoformat(row["end_date"]) >= today
        assert "application_url" not in row
        assert row["reservation_available"] is False
