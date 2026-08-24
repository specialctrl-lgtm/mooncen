from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from html import escape
import json
import os
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_shinan as shinan


TARGET = {
    "provider": shinan.SHINAN_FAMILY_PROVIDER,
    "url": shinan.SHINAN_FAMILY_LIST_URL,
}
CSRF = "01234567-89ab-cdef-0123-456789abcdef"


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    event_start: str
    event_end: str
    apply_start: str
    apply_end: str
    place1: str
    place2: str
    target: str = "신안군 가족"
    rounds: int = 2
    source_status: str = "접수중"
    status_class: str = "c0"
    current: int = 1
    capacity: int = 20
    wait: int = 5


class Response:
    def __init__(
        self,
        body: str | bytes,
        url: str,
        *,
        content_type: str,
        status_code: int = 200,
        final_url: str | None = None,
        history: tuple[Any, ...] = (),
    ) -> None:
        self.status_code = status_code
        self.content = body.encode("utf-8") if isinstance(body, str) else body
        self.url = final_url or url
        self.headers = {"Content-Type": content_type}
        self.history = history


class DummySession:
    def close(self) -> None:
        return None


def _courses(*, no_current: bool = False) -> list[Course]:
    rows = [
        Course(
            "208",
            "찾아가는 부모성장교실",
            "2026-08-12",
            "2026-08-12",
            "2026-07-20 00:00",
            "2026-08-14 00:00",
            "전남광주통합특별시 신안군 압해읍 압해로 876-22",
            "신안군가족센터 교류소통실",
            current=3,
            capacity=50,
            wait=10,
        ),
        Course(
            "207",
            "상시프로그램 신비한 과학교실",
            "2026-08-08",
            "2026-09-12",
            "2026-07-21 09:00",
            "2026-09-12 00:00",
            "전남광주통합특별시 신안군 압해읍 압해로 876-22",
            "신안군가족센터 공동육아나눔터",
            target="관내 8~10세 자녀",
            rounds=5,
            current=4,
            capacity=8,
            wait=2,
        ),
        Course(
            "206",
            "북부권 이중언어 부모자녀 프로그램",
            "2026-07-18",
            "2026-07-22",
            "2026-07-09 00:00",
            "2026-07-31 00:00",
            "전남광주통합특별시 신안군 지도읍 읍내길 67-5",
            "지도읍사무소 2층 회의실",
            rounds=5,
            current=5,
            capacity=15,
            wait=0,
        ),
        Course(
            "205",
            "종료됐지만 접수중인 코칭 1",
            "2026-06-01",
            "2026-06-27",
            "2026-03-02 09:00",
            "2026-08-28 23:00",
            "전라남도 신안군 압해읍 압해로 876-22",
            "신안군가족센터",
        ),
        Course(
            "204",
            "종료됐지만 접수중인 코칭 2",
            "2026-05-01",
            "2026-05-27",
            "2026-03-02 09:00",
            "2026-08-28 23:00",
            "전라남도 신안군 압해읍 압해로 876-22",
            "도초면 다가온",
        ),
        Course(
            "203",
            "과거 한국어교육 1",
            "2026-03-01",
            "2026-04-15",
            "2026-03-02 09:00",
            "2026-10-30 23:00",
            "전라남도 신안군 압해읍 압해로 876-22",
            "신안군가족센터",
        ),
        Course(
            "202",
            "과거 한국어교육 2",
            "2026-02-01",
            "2026-03-15",
            "2026-02-01 09:00",
            "2026-10-30 23:00",
            "전라남도 신안군 압해읍 압해로 876-22",
            "신안군가족센터",
        ),
        Course(
            "201",
            "과거 가족 코칭",
            "2026-01-01",
            "2026-02-28",
            "2026-01-01 09:00",
            "2026-12-31 18:00",
            "전라남도 신안군 압해읍 압해로 876-22",
            "신안군가족센터",
        ),
    ]
    if no_current:
        rows = [
            replace(
                item,
                event_start="2025-01-01",
                event_end="2025-02-01",
                apply_start="2025-01-01 09:00",
                apply_end="2025-12-31 18:00",
            )
            for item in rows
        ]
    return rows


def _card(
    course: Course,
    *,
    identity: str | None = None,
    missing_control: bool = False,
    wrong_control_identity: bool = False,
) -> str:
    source_identity = identity or course.identity
    control_identity = (
        str(int(source_identity) + 1) if wrong_control_identity else source_identity
    )
    control = ""
    if not missing_control:
        control = (
            f'<a href="javascript:void(0);" '
            f'onclick="send(\'{control_identity}\',\'{escape(course.title)}\',\'center\')">'
            "신청하기</a>"
        )
    venue = f"{course.place1} {course.place2}"
    return f"""
      <li class="clearfix">
        <div class="txt">
          <p class="tit"><a href="javascript:void(0);"
            onclick="send('{source_identity}','{escape(course.title)}','web')">{escape(course.title)}</a></p>
          <ul>
            <li><p><b>회차정보</b>총 {course.rounds}회</p></li>
            <li><p><b>행사기간</b>{course.event_start} ~ {course.event_end}</p></li>
            <li><p><b>접수기간</b>{course.apply_start} ~ {course.apply_end}</p></li>
            <li><p><b>진행장소</b>{escape(venue)}<a class="place">오시는길</a></p></li>
          </ul>
        </div>
        <div class="util">
          <div class="loc">전남광주 &gt; 신안군</div>
          <div class="state">{control}<span class="{course.status_class}">{course.source_status}</span></div>
        </div>
      </li>
    """


def _list_html(
    all_rows: list[Course],
    page: int,
    *,
    declared_pager_max: int = 1,
    first_mutation: bool = False,
    sentinel_data: bool = False,
    duplicate_identity: bool = False,
    missing_control: bool = False,
    wrong_control_identity: bool = False,
    owner_code: str = "A016",
) -> str:
    start = (page - 1) * shinan.SHINAN_PAGE_SIZE
    visible = list(all_rows[start : start + shinan.SHINAN_PAGE_SIZE])
    if first_mutation and visible:
        visible[0] = replace(visible[0], title=visible[0].title + " 변경")
    if sentinel_data and not visible:
        visible = [all_rows[-1]]
    cards: list[str] = []
    for index, course in enumerate(visible):
        identity = None
        if duplicate_identity and page == 2 and index == 0:
            identity = all_rows[0].identity
        cards.append(
            _card(
                course,
                identity=identity,
                missing_control=missing_control,
                wrong_control_identity=wrong_control_identity,
            )
        )
    programme = (
        "".join(cards)
        if cards
        else '<li style="display:block"><p style="text-align:center">프로그램 목록이 존재하지 않습니다.</p></li>'
    )
    pager = "".join(
        f'<a href="{shinan.SHINAN_FAMILY_LIST_PATH}?rows=5&cpage={value}">{value}</a>'
        for value in range(1, declared_pager_max + 1)
    )
    return f"""
      <html><head><title>신안군 가족센터&gt;프로그램안내&gt;프로그램신청</title></head>
      <body>
        <form id="searchForm" action="{shinan.SHINAN_FAMILY_LIST_PATH}" method="get">
          <input type="hidden" name="rows" value="5">
          <input type="hidden" name="cpage" value="{page}">
          <input type="hidden" name="status" value="">
          <input type="hidden" name="area" value="{owner_code}">
          <input type="hidden" name="area_detail" value="D197">
        </form>
        <div class="program_list"><ul>{programme}</ul></div>
        <div class="pagination">{pager}</div>
      </body></html>
    """


def _detail_shell(
    course: Course,
    *,
    identity: str | None = None,
    csrf: str = CSRF,
    include_control: bool = True,
) -> str:
    source_identity = identity or course.identity
    control = (
        '<a id="applyBtn" href="javascript:applysMethods.modal.openApply();">신청하기</a>'
        if include_control
        else ""
    )
    return f"""
      <html><head>
        <title>신안군 가족센터&gt;프로그램안내&gt;프로그램신청</title>
        <meta name="_csrf" content="{csrf}">
      </head><body>
        <div class="program_view"></div>
        <input type="hidden" name="familynet_pg_no" value="{source_identity}">
        <input type="hidden" name="area" value="A016">
        <input type="hidden" name="area_detail" value="D197">
        {control}
        <a href="{shinan.SHINAN_FAMILY_LOGIN_PATH}">로그인</a>
        <script>
          common.ajaxPost("/recruitReceipt/getView.do", {{seq: "{source_identity}"}});
          common.ajaxPost("/recruitReceipt/loginCheck.do", {{}});
        </script>
      </body></html>
    """


def _detail_payload(
    course: Course,
    *,
    identity: str | None = None,
    title: str | None = None,
    area: str = "A016",
    source_status: str | None = None,
    apply_yn: bool = False,
    impossible_capacity: bool = False,
) -> dict[str, Any]:
    current = course.capacity + course.wait + 1 if impossible_capacity else course.current
    episodes = [
        {
            "episode": "1",
            "episode_dt": (
                f"{course.event_start} 10:00 ~ "
                f"{course.event_end if course.event_start != course.event_end else course.event_start} 12:00"
            ),
        }
    ]
    return {
        "view": {
            "familynet_pg_no": identity or course.identity,
            "title": title or course.title,
            "area": area,
            "area_detail": "D197",
            "area_nm": "전남광주",
            "area_detail_nm": "신안군",
            "program_start_date": course.event_start,
            "program_end_date": course.event_end,
            "reception_date_start_time": course.apply_start,
            "reception_date_end_time": course.apply_end,
            "program_status_nm": source_status or course.source_status,
            "program_place1": course.place1,
            "program_place2": course.place2,
            "participation_target": course.target,
            "curr_apply_seq": str(current),
            "recruit_personal": str(course.capacity),
            "waiting_personal": str(course.wait),
            "program_conts": "discard me 061-240-0000 staff@example.org",
            "attachs": [{"name": "discard.hwp"}],
            "images": [{"images": "/discard.jpg"}],
        },
        "apply_yn": apply_yn,
        "episode": episodes,
    }


class Fixture:
    def __init__(
        self,
        rows: list[Course] | None = None,
        **mutations: Any,
    ) -> None:
        self.rows = rows or _courses()
        self.mutations = mutations
        self.lock = threading.Lock()
        self.page_calls: dict[int, int] = {}
        self.detail_calls: list[str] = []
        self.json_calls: list[str] = []
        self.unexpected_urls: list[str] = []

    def session_factory(self) -> DummySession:
        return DummySession()

    def html_fetcher(self, session: Any, url: str, timeout: int) -> Response:
        del session, timeout
        parsed = urlparse(url)
        if parsed.path == shinan.SHINAN_FAMILY_LIST_PATH:
            page = int((parse_qs(parsed.query).get("cpage") or ["1"])[0])
            with self.lock:
                call = self.page_calls.get(page, 0) + 1
                self.page_calls[page] = call
            html = _list_html(
                self.rows,
                page,
                declared_pager_max=self.mutations.get("declared_pager_max", 1),
                first_mutation=(
                    bool(self.mutations.get("first_mutation")) and page == 1 and call > 1
                ),
                sentinel_data=(
                    bool(self.mutations.get("sentinel_data"))
                    and page == ((len(self.rows) + 4) // 5) + 1
                    and call > 1
                ),
                duplicate_identity=bool(self.mutations.get("duplicate_identity")),
                missing_control=bool(self.mutations.get("missing_control")),
                wrong_control_identity=bool(self.mutations.get("wrong_control_identity")),
                owner_code=self.mutations.get("owner_code", "A016"),
            )
            return Response(html, url, content_type="text/html; charset=UTF-8")
        if parsed.path == shinan.SHINAN_FAMILY_DETAIL_PATH:
            identity = (parse_qs(parsed.query).get("seq") or [""])[0]
            course = next(item for item in self.rows if item.identity == identity)
            with self.lock:
                self.detail_calls.append(identity)
            shell = _detail_shell(
                course,
                identity=(
                    str(int(identity) + 1)
                    if self.mutations.get("shell_identity_mismatch")
                    else identity
                ),
                csrf="bad" if self.mutations.get("bad_csrf") else CSRF,
                include_control=not self.mutations.get("missing_detail_control", False),
            )
            return Response(
                shell,
                url,
                content_type=self.mutations.get(
                    "detail_content_type", "text/html; charset=UTF-8"
                ),
                final_url=(url + "&redirected=1")
                if self.mutations.get("detail_redirect")
                else None,
                history=(object(),) if self.mutations.get("detail_history") else (),
            )
        self.unexpected_urls.append(url)
        raise AssertionError(f"unexpected HTML request: {url}")

    def json_fetcher(
        self,
        session: Any,
        url: str,
        identity: str,
        csrf: str,
        timeout: int,
    ) -> Response:
        del session, timeout
        assert url == shinan.SHINAN_FAMILY_VIEW_API_URL
        assert csrf == CSRF
        course = next(item for item in self.rows if item.identity == identity)
        with self.lock:
            self.json_calls.append(identity)
        payload = _detail_payload(
            course,
            identity=(
                str(int(identity) + 1)
                if self.mutations.get("api_identity_mismatch")
                else identity
            ),
            title=(course.title + " changed")
            if self.mutations.get("api_title_mismatch")
            else None,
            area="A999" if self.mutations.get("api_owner_mismatch") else "A016",
            source_status="완료"
            if self.mutations.get("api_status_mismatch")
            else None,
            apply_yn=bool(self.mutations.get("anonymous_applied")),
            impossible_capacity=bool(self.mutations.get("impossible_capacity")),
        )
        if self.mutations.get("api_date_mismatch"):
            payload["view"]["program_end_date"] = "2026-12-31"
        if self.mutations.get("api_projection") and identity == "208":
            payload["view"]["program_start_date"] = "2026-07-01"
            payload["episode"] = [
                {
                    "episode": "1",
                    "episode_dt": "2026-07-01 10:00 ~ 2026-07-01 12:00",
                },
                {
                    "episode": "2",
                    "episode_dt": "2026-08-12 10:00 ~ 2026-08-12 12:00",
                },
            ]
        body = "not-json" if self.mutations.get("invalid_json") else json.dumps(payload, ensure_ascii=False)
        return Response(
            body,
            url,
            content_type=self.mutations.get(
                "json_content_type", "application/json;charset=UTF-8"
            ),
            final_url=(url + "?redirected=1")
            if self.mutations.get("json_redirect")
            else None,
            history=(object(),) if self.mutations.get("json_history") else (),
        )

    def collect(self, **kwargs: Any):
        return shinan.collect_shinan_education(
            TARGET,
            cutoff=date(2026, 7, 21),
            max_pages=kwargs.pop("max_pages", 10),
            detail_limit=kwargs.pop("detail_limit", 20),
            workers=kwargs.pop("workers", 3),
            session_factory=self.session_factory,
            html_fetcher=self.html_fetcher,
            json_fetcher=self.json_fetcher,
            **kwargs,
        )


def test_collects_complete_hidden_page_and_current_detail_union() -> None:
    fixture = Fixture()
    rows, parser, meta = fixture.collect()

    assert parser == shinan.SHINAN_PARSER
    assert [row["provider_course_id"] for row in rows] == [
        "family_center:208",
        "family_center:207",
        "family_center:206",
    ]
    assert meta["source_rows"] == 8
    assert meta["page_counts"] == {1: 5, 2: 3}
    assert meta["empty_sentinel_page"] == 3
    assert meta["declared_pager_max"] == 1
    assert meta["pagination_declared_underflow"] == 1
    assert meta["list_requests"] == 6
    assert meta["current_source_count"] == 3
    assert meta["expired_but_open_quarantined"] == 5
    assert meta["detail_pages"] == 3
    assert meta["detail_api_requests"] == 3
    assert meta["pagination_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert sorted(fixture.detail_calls) == ["206", "207", "208"]
    assert sorted(fixture.json_calls) == ["206", "207", "208"]
    assert fixture.unexpected_urls == []


def test_emitted_rows_use_exact_owner_branch_venue_and_source_ids() -> None:
    fixture = Fixture()
    rows, _, _ = fixture.collect()
    first = rows[0]

    assert first["provider"] == shinan.SHINAN_FAMILY_PROVIDER
    assert first["branch"] == "신안군 가족센터"
    assert first["branch_code"] == "shinan_family_center"
    assert first["venue_name"] == "신안군가족센터 교류소통실"
    assert first["raw_url"] == shinan.shinan_family_detail_url("208")
    assert first["application_url"] == first["raw_url"]
    assert first["application_method_raw"] == "온라인 신청(로그인)"
    assert first["reservation_available"] is True
    assert first["status"] == "OPEN"
    assert first["capacity_current"] == 3
    assert first["capacity_total"] == 50
    assert first["capacity_wait_total"] == 10
    assert first["fee"] == "요금 별도 안내"
    assert first["schedule_raw"] == "총 2회 · 10:00~12:00"
    assert first["raw_fields"]["application_control_contract"] == (
        "login_gated_modal_not_requested"
    )


def test_list_period_may_be_an_episode_bound_projection_of_full_api_period() -> None:
    rows, _, meta = Fixture(api_projection=True).collect(workers=1)

    assert meta["configured_collection_error"] == ""
    row = next(item for item in rows if item["provider_course_id"] == "family_center:208")
    assert row["start_date"] == "2026-07-01"
    assert row["end_date"] == "2026-08-12"
    assert row["raw_fields"]["source_list_event_period"] == (
        "2026-08-12 ~ 2026-08-12"
    )
    assert row["raw_fields"]["list_period_projection_verified"] is True


def test_pii_and_non_allowlisted_detail_payload_are_discarded() -> None:
    rows, _, _ = Fixture().collect()
    serialized = json.dumps(rows, ensure_ascii=False)

    assert "061-240-0000" not in serialized
    assert "staff@example.org" not in serialized
    assert "discard.hwp" not in serialized
    assert "discard.jpg" not in serialized
    assert "csrf" not in serialized.lower()
    assert "program_conts" not in serialized


def test_no_current_snapshot_is_valid_without_detail_or_login_requests() -> None:
    fixture = Fixture(_courses(no_current=True))
    rows, _, meta = fixture.collect(detail_limit=0)

    assert rows == []
    assert meta["source_rows"] == 8
    assert meta["current_source_count"] == 0
    assert meta["pagination_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert fixture.detail_calls == []
    assert fixture.json_calls == []
    assert fixture.unexpected_urls == []


def test_detail_limit_never_authorizes_partial_output() -> None:
    rows, _, meta = Fixture().collect(detail_limit=2)

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]
    assert meta["pagination_complete"] is False


def test_page_cap_never_authorizes_partial_output() -> None:
    rows, _, meta = Fixture().collect(max_pages=2)

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "before an empty sentinel" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    "mutation,error",
    [
        ({"duplicate_identity": True}, "duplicate source identities"),
        ({"first_mutation": True}, "stability recheck changed"),
        ({"sentinel_data": True}, "stability recheck changed"),
        ({"missing_control": True}, "OPEN control missing"),
        ({"wrong_control_identity": True}, "application identity mismatch"),
        ({"owner_code": "A999"}, "owner/pagination form values changed"),
        ({"declared_pager_max": 3}, "declared pager points beyond"),
    ],
)
def test_list_contract_drift_fails_closed(
    mutation: dict[str, Any],
    error: str,
) -> None:
    rows, _, meta = Fixture(**mutation).collect()

    assert rows == []
    assert error in meta["configured_collection_error"]
    assert meta["pagination_complete"] is False


@pytest.mark.parametrize(
    "mutation,error",
    [
        ({"shell_identity_mismatch": True}, "shell identity mismatch"),
        ({"bad_csrf": True}, "invalid CSRF contract"),
        ({"missing_detail_control": True}, "login-gated control missing"),
        ({"api_identity_mismatch": True}, "API identity mismatch"),
        ({"api_title_mismatch": True}, "title mismatch"),
        ({"api_owner_mismatch": True}, "API owner codes changed"),
        ({"api_status_mismatch": True}, "list/API status mismatch"),
        ({"api_date_mismatch": True}, "API/episode date mismatch"),
        ({"anonymous_applied": True}, "anonymous application state changed"),
        ({"impossible_capacity": True}, "impossible capacity"),
    ],
)
def test_detail_contract_drift_fails_the_whole_snapshot(
    mutation: dict[str, Any],
    error: str,
) -> None:
    rows, _, meta = Fixture(**mutation).collect(workers=1)

    assert rows == []
    assert error in meta["configured_collection_error"]
    assert meta["output_rows"] == 0


@pytest.mark.parametrize(
    "mutation,error",
    [
        ({"detail_content_type": "text/plain"}, "unexpected Content-Type"),
        ({"detail_redirect": True}, "unexpected final URL"),
        ({"detail_history": True}, "redirects are not permitted"),
        ({"json_content_type": "text/html"}, "unexpected Content-Type"),
        ({"json_redirect": True}, "unexpected final URL"),
        ({"json_history": True}, "redirects are not permitted"),
        ({"invalid_json": True}, "invalid JSON"),
    ],
)
def test_detail_transport_contract_fails_closed(
    mutation: dict[str, Any],
    error: str,
) -> None:
    rows, _, meta = Fixture(**mutation).collect(workers=1)

    assert rows == []
    assert error in meta["configured_collection_error"]


@pytest.mark.parametrize(
    "target",
    [
        {"provider": "WRONG", "url": shinan.SHINAN_FAMILY_LIST_URL},
        {
            "provider": shinan.SHINAN_FAMILY_PROVIDER,
            "url": shinan.SHINAN_FAMILY_LIST_URL + "?rows=5&cpage=1",
        },
        {
            "provider": shinan.SHINAN_FAMILY_PROVIDER,
            "url": "https://www.jntle.kr/main/uDamoaLecture/1?queryType=4691",
        },
    ],
)
def test_wrong_owner_or_alias_target_is_rejected(target: dict[str, str]) -> None:
    rows, _, meta = shinan.collect_shinan_education(target)

    assert rows == []
    assert meta["configured_collection_error"]


def test_url_builders_reject_nonpositive_identities_and_pages() -> None:
    assert shinan.shinan_family_list_url(4).endswith("rows=5&cpage=4")
    assert shinan.shinan_family_detail_url("271388").endswith("?seq=271388")
    with pytest.raises(shinan.ShinanContractError):
        shinan.shinan_family_list_url(0)
    with pytest.raises(shinan.ShinanContractError):
        shinan.shinan_family_detail_url("0")
    with pytest.raises(shinan.ShinanContractError):
        shinan.shinan_family_detail_url("1&admin=true")


def test_discovery_and_owner_audit_record_exact_live_boundaries() -> None:
    audit = shinan.SHINAN_DISCOVERY_AUDIT

    assert audit["checked_on"] == "2026-07-21"
    assert audit["jntle_total"] == 46
    assert audit["jntle_page_counts"] == [15, 15, 15, 1]
    assert audit["jntle_current_or_future"] == 0
    assert audit["family_center_total"] == 18
    assert audit["family_center_page_counts"] == [5, 5, 5, 3]
    assert audit["family_center_declared_pager_max"] == 3
    assert audit["family_center_observed_data_pages"] == 4
    assert audit["family_center_current_or_future"] == 11
    assert audit["family_center_ended_but_still_open_quarantined"] == 7
    assert audit["museum_total"] == 22
    assert audit["agricultural_dedicated_board_total"] == 255
    assert audit["library_current_course_rows"] == 0
    assert shinan.SHINAN_CANDIDATE_AUDIT[shinan.SHINAN_JNTLE_CANDIDATE_ID][
        "decision"
    ] == "exclude_provincial_secondary_aggregate"
    assert shinan.SHINAN_OWNER_BOUNDARY_AUDIT[shinan.SHINAN_FAMILY_PROVIDER][
        "exact_branch"
    ] == "신안군 가족센터"


@pytest.mark.skipif(
    os.environ.get("SHINAN_FAMILYNET_LIVE") != "1",
    reason="set SHINAN_FAMILYNET_LIVE=1 for the live opt-in contract test",
)
def test_live_family_center_contract_opt_in() -> None:
    rows, parser, meta = shinan.collect_shinan_education(
        TARGET,
        cutoff=date.today(),
        timeout=30,
        max_pages=40,
        detail_limit=100,
        workers=6,
    )

    assert parser == shinan.SHINAN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["pagination_complete"] is True
    assert meta["source_rows"] >= len(rows)
    assert meta["empty_sentinel_page"] == meta["data_pages"] + 1
    assert meta["applicant_form_requests"] == 0
    assert meta["login_requests"] == 0
    assert len({row["provider_course_id"] for row in rows}) == len(rows)
    assert all(row["provider"] == shinan.SHINAN_FAMILY_PROVIDER for row in rows)
    assert all(row["branch"] == shinan.SHINAN_FAMILY_BRANCH for row in rows)
