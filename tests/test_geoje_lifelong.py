from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import html
from typing import Any
from urllib.parse import parse_qs, urlparse

from Crawler import municipal_geoje as geoje


@dataclass
class Target:
    provider: str = geoje.GEOJE_LIFELONG_PROVIDER
    url: str = geoje.GEOJE_LIFELONG_URL


class FakeResponse:
    def __init__(self, url: str, body: str) -> None:
        self.url = url
        self.text = body
        self.content = body.encode("utf-8")
        self.status_code = 200
        self.history: list[Any] = []


def _source_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for number in range(12, 0, -1):
        identity = f"COURSE_{number:013d}"
        current = number in {12, 11}
        rows.append(
            {
                "id": identity,
                "code": "SC001" if number != 3 else "SC002",
                "image": f"FILE_{number:015d}" if number % 2 else "",
                "title": f"거제 평생 강좌 {number}",
                "status": "접수중" if number == 12 else "접수마감",
                "apply": (
                    "2026-07-01 09:00 ~ 2026-07-30 18:00"
                    if number == 12
                    else "2026-06-01 09:00 ~ 2026-06-15 18:00"
                ),
                "period": (
                    f"2026-07-{24 if number == 12 else 25:02d} 10:00 ~ "
                    f"2026-07-{24 if number == 12 else 25:02d} 12:00"
                    if current
                    else "2026-06-01 10:00 ~ 2026-07-01 12:00"
                ),
                "schedule": "매주 금요일 10:00~12:00",
                "venue": "거제시 평생학습관 교육실",
                "fee": "무료",
                "regular_current": "3",
                "regular_total": "10",
                "wait_current": "1",
                "wait_total": "5",
            }
        )
    return rows


class FakeSite:
    def __init__(
        self,
        *,
        bad_sentinel: bool = False,
        mutate_boundary: bool = False,
        duplicate_identity: bool = False,
        unknown_status: bool = False,
        bad_detail_title: bool = False,
        private_detail: bool = False,
        fail_detail_once: bool = False,
    ) -> None:
        self.rows = _source_rows()
        if duplicate_identity:
            self.rows[-1]["id"] = self.rows[-2]["id"]
        if unknown_status:
            self.rows[0]["status"] = "새상태"
        self.bad_sentinel = bad_sentinel
        self.mutate_boundary = mutate_boundary
        self.bad_detail_title = bad_detail_title
        self.private_detail = private_detail
        self.fail_detail_once = fail_detail_once
        self.page_calls: Counter[int] = Counter()
        self.detail_calls: Counter[str] = Counter()
        self.sessions: list[FakeSession] = []
        self.registration_fetches = 0
        self.applicant_fetches = 0

    def session_factory(self) -> "FakeSession":
        value = FakeSession(self)
        self.sessions.append(value)
        return value

    def page(self, page: int) -> str:
        self.page_calls[page] += 1
        total = len(self.rows)
        last = (total + geoje.GEOJE_PAGE_SIZE - 1) // geoje.GEOJE_PAGE_SIZE
        if page <= last:
            page_rows = deepcopy(
                self.rows[(page - 1) * geoje.GEOJE_PAGE_SIZE : page * geoje.GEOJE_PAGE_SIZE]
            )
        elif page == last + 1 and self.bad_sentinel:
            page_rows = [deepcopy(self.rows[-1])]
        else:
            page_rows = []
        if self.mutate_boundary and page == 1 and self.page_calls[page] > 1:
            page_rows[0]["title"] += " 변경"
        body = "".join(_list_row(row) for row in page_rows)
        if not body:
            body = '<tr><td colspan="8">조회된 결과가 없습니다.</td></tr>'
        headers = "".join(f"<th>{value}</th>" for value in geoje._LIST_HEADERS)
        return f"""
        <html><body>
          <p class="t_page">페이지 : {page} /{last}　전체 게시물 : {total}</p>
          <form id="frm_searchs" method="post">
            <input name="currentPageNo" value="{page}">
            <input name="groupId" value="{geoje.GEOJE_LIFELONG_GROUP_ID}">
          </form>
          <table class="responTable"><thead><tr>{headers}</tr></thead>
            <tbody>{body}</tbody>
          </table>
        </body></html>
        """

    def detail(self, row: dict[str, str]) -> str:
        identity = row["id"]
        title = "다른 강좌" if self.bad_detail_title else row["title"]
        login_id = "private-user" if self.private_detail else ""
        application = (
            '<a class="bg_btn" href="javascript:couRegist();">접수하기</a>'
            if row["status"] == "접수중"
            else ""
        )
        return f"""
        <html><body>
          <div class="life_tit"><h3 class="dan_h3">{html.escape(title)}</h3></div>
          <form id="couForm">
            <input name="COURSE_ID" value="{identity}">
            <input name="ORGNZT_ID" value="{geoje.GEOJE_LIFELONG_GROUP_ID}">
            <input name="ORGNZT_NM" value="{geoje.GEOJE_SOURCE_ORGANIZER}">
            <input name="TITLE" value="{html.escape(row['title'])}">
            <input name="loginId" value="{login_id}">
            <input name="loginNm" value="">
            <input name="loginBirthDay" value="">
            <div class="life_program">
              <table class="responTable"><tbody>
                <tr><th>학습기관</th><td>{geoje.GEOJE_SOURCE_ORGANIZER}</td>
                    <th>학습기간</th><td>{row['period']}</td>
                    <th>접수기간</th><td>{row['apply']}</td></tr>
                <tr><th>강 사 명</th><td>개인강사 010-1111-2222</td>
                    <th>수 강 료</th><td>{row['fee']}</td>
                    <th>재료비</th><td>없음</td></tr>
                <tr><th>교육대상</th><td>성인</td>
                    <th>교육주기</th><td>{row['schedule']}</td>
                    <th>모집인원/대기인원</th>
                    <td>{row['regular_total']}명/ {row['wait_total']}명</td></tr>
                <tr><th>신청대상</th><td>거제시민</td></tr>
                <tr><th>교육장소</th><td>{row['venue']}</td></tr>
                <tr><th>상세내용</th></tr>
                <tr><td>private@example.com 및 계좌·연락처가 있는 비저장 본문</td></tr>
                <tr><th>사진</th><td></td></tr>
                <tr><th>강의계획서</th><td>첨부파일</td></tr>
              </tbody></table>
            </div>
            {application}
          </form>
          <script>
            function couRegist() {{
              $("#couForm").attr(
                "action",
                "/com/requestPage.do?selMenuNo=1030600&returnUrl=/educenter/b1020201_couRegist.do"
              ).submit();
            }}
          </script>
        </body></html>
        """


def _list_row(row: dict[str, str]) -> str:
    call = (
        "javascript:couDetail("
        f"'{row['id']}','{row['code']}','{row['image']}','','');"
    )
    anchor_id = f"{row['id']}||{row['code']}"

    def link(value: str) -> str:
        return (
            f'<a class="detailItem" id="{anchor_id}" '
            f'href="{call}">{html.escape(value)}</a>'
        )

    capacity = (
        f"모집인원( {row['regular_current']} /{row['regular_total']}) "
        f"대기인원( {row['wait_current']} /{row['wait_total']}) "
        f'<a class="detailStudent" href="javascript:couStudentView('
        f"'{row['id']}','1');\">신청인원보기</a>"
    )
    cells = [
        link(geoje.GEOJE_SOURCE_ORGANIZER),
        link(row["title"]),
        capacity,
        link(row["apply"]),
        link(f"{row['period']} {row['schedule']}"),
        link(row["venue"]),
        link(row["fee"]),
        link(row["status"]),
    ]
    return "<tr>" + "".join(f"<td>{value}</td>" for value in cells) + "</tr>"


class FakeSession:
    def __init__(self, site: FakeSite) -> None:
        self.site = site
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def get(self, url: str, **_kwargs: Any) -> FakeResponse:
        if url == geoje.GEOJE_LIFELONG_URL:
            return FakeResponse(url, self.site.page(1))
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        return_url = query.get("returnUrl", [""])[0]
        if return_url.endswith("couRegist.do"):
            self.site.registration_fetches += 1
            raise AssertionError("registration endpoint must not be fetched")
        if return_url.endswith("_view.do"):
            self.site.applicant_fetches += 1
            raise AssertionError("applicant list must not be fetched")
        assert parsed.scheme == "https"
        assert parsed.netloc == geoje.GEOJE_LIFELONG_HOST
        assert parsed.path == geoje.GEOJE_LIFELONG_PATH
        assert return_url == "/educenter/b1020201_detail.do"
        identity = query["COURSE_ID"][0]
        self.site.detail_calls[identity] += 1
        if self.site.fail_detail_once and self.site.detail_calls[identity] == 1:
            raise TimeoutError("synthetic transient failure")
        row = next(value for value in self.site.rows if value["id"] == identity)
        return FakeResponse(url, self.site.detail(row))

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        assert url == geoje.GEOJE_LIFELONG_URL
        data = kwargs["data"]
        assert data == geoje.geoje_lifelong_page_payload(int(data["currentPageNo"]))
        page = int(data["currentPageNo"])
        return FakeResponse(url, self.site.page(page))


def _collect(site: FakeSite, **kwargs: Any):
    options = {
        "timeout": 5,
        "max_pages": 5,
        "detail_limit": 5,
        "today": "2026-07-23",
        "max_workers": 2,
        "session_factory": site.session_factory,
    }
    options.update(kwargs)
    return geoje.collect_geoje_lifelong_education_courses(
        Target(),
        **options,
    )


def test_target_ids_urls_and_separate_owner_contract() -> None:
    assert geoje.is_geoje_lifelong_target(Target())
    assert not geoje.is_geoje_lifelong_target(
        Target(url=geoje.GEOJE_LIFELONG_URL + "&currentPageNo=2")
    )
    assert not geoje.is_geoje_lifelong_target(
        Target(provider=geoje.GEOJE_LIBRARY_OWNER_PROVIDER)
    )
    assert geoje.GEOJE_LIFELONG_CANDIDATE_ID == "MUNI_IR_0B3EE68CBAFB"
    assert geoje.GEOJE_LIBRARY_REVIEW_CANDIDATE_ID == "MUNI_IR_88A4E9D40A8C"
    assert geoje.GEOJE_BOTANIC_GARDEN_PROVIDER == "MUNI_WWW_GEOJE_GO_KR_GBG_EDU"
    assert geoje.geoje_lifelong_detail_url("bad") == ""
    assert "b1020201_detail.do" in geoje.geoje_lifelong_detail_url(
        "COURSE_0000000000001"
    )


def test_complete_snapshot_empty_sentinel_details_controls_branch_and_pii() -> None:
    site = FakeSite()
    rows, parser, meta = _collect(site)

    assert parser == geoje.GEOJE_LIFELONG_PARSER
    assert len(rows) == 2
    assert meta["source_total"] == meta["declared_total"] == 12
    assert meta["data_pages"] == 2
    assert meta["list_requests"] == meta["required_list_requests"] == 5
    assert meta["sentinel_count"] == 0
    assert meta["stable_rechecks"] == {"1": True, "2": True}
    assert meta["current_count"] == meta["detail_pages"] == 2
    assert meta["expired_count"] == 10
    assert meta["source_organizer_counts"] == {"평생학습센터": 12}
    assert meta["snapshot_complete"] is True
    assert meta["application_control_count"] == 1
    assert meta["privacy_violations"] == 0
    assert meta["configured_collection_error"] == ""
    assert site.registration_fetches == site.applicant_fetches == 0
    assert all(session.closed for session in site.sessions)

    by_title = {row["title"]: row for row in rows}
    opened = by_title["거제 평생 강좌 12"]
    closed = by_title["거제 평생 강좌 11"]
    assert opened["branch"] == "거제시평생학습관"
    assert opened["provider_organizer"] == "평생학습센터"
    assert opened["status"] == "OPEN"
    assert opened["reservation_available"] is True
    assert opened["application_url"] == opened["raw_url"]
    assert (
        opened["raw_fields"]["application_control_contract"]
        == "current_bg_btn_couRegist"
    )
    assert closed["status"] == "CLOSED"
    assert closed["application_url"] == ""
    assert all(row["target"] == "성인" for row in rows)
    serialized = repr(rows)
    assert "010-1111-2222" not in serialized
    assert "private@example.com" not in serialized
    assert "첨부파일" not in serialized


def test_caps_sentinel_boundaries_identity_and_status_fail_closed() -> None:
    rows, _parser, meta = _collect(FakeSite(), max_pages=4)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(), detail_limit=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(bad_sentinel=True))
    assert rows == []
    assert "post-last page is not empty" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(mutate_boundary=True))
    assert rows == []
    assert "stable boundary recheck changed" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(duplicate_identity=True))
    assert rows == []
    assert "duplicate source identities" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(unknown_status=True))
    assert rows == []
    assert "unknown source status" in meta["configured_collection_error"]


def test_detail_title_and_authenticated_member_contracts_fail_closed() -> None:
    rows, _parser, meta = _collect(FakeSite(bad_detail_title=True))
    assert rows == []
    assert "visible title mismatch" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(private_detail=True))
    assert rows == []
    assert "authenticated member data exposed" in meta["configured_collection_error"]


def test_retry_archived_dedupe_and_managed_session_contract() -> None:
    site = FakeSite(fail_detail_once=True)
    rows, _parser, meta = _collect(site)
    assert len(rows) == 2
    assert meta["network_retry_count"] == 2

    archived = FakeSite()
    rows, _parser, meta = _collect(
        archived, today="2100-01-01", detail_limit=0
    )
    assert rows == []
    assert meta["source_total"] == 12
    assert meta["current_count"] == 0
    assert meta["detail_attempts"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True

    rows, _parser, meta = _collect(
        FakeSite(), dedupe_rows=lambda source: source[:1]
    )
    assert rows == []
    assert "dedupe changed complete row count" in meta["configured_collection_error"]

    rows, _parser, meta = geoje.collect_geoje_lifelong_education_courses(
        Target(), max_pages=5, detail_limit=5
    )
    assert rows == []
    assert meta["configured_collection_error"] == (
        "managed session_factory injection is required"
    )
