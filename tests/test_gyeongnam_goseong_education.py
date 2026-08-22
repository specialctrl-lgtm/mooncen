from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import municipal_gyeongnam_goseong as goseong


def _target(
    *,
    provider: str = goseong.GOSEONG_GN_PROVIDER,
    url: str = goseong.GOSEONG_GN_URL,
) -> dict[str, str]:
    return {
        "provider": provider,
        "url": url,
        "name": "경상남도 고성군 교육",
        "branch": "경상남도 고성군",
    }


RECORDS = (
    {
        "id": "103",
        "title": "온라인 미래 강좌",
        "status": "접수중",
        "status_class": "receipt",
        "venue": "경남 고성군 평생학습관",
        "target": "전군민",
        "apply": "2099-07-01 ~ 2099-07-31",
        "period": "2099-08-01 ~ 2099-08-31",
        "method": "인터넷",
        "organizer": "고성군청 교육청소년과 / 평생학습관 / 055-670-2614",
        "control": "신청하기",
    },
    {
        "id": "102",
        "title": "방문접수 장기 강좌",
        "status": "접수마감",
        "status_class": "deadline",
        "venue": "고성읍행정복지센터",
        "target": "18세 이상 군민",
        "apply": "2099-06-01 ~ 2099-06-10",
        "period": "2099-06-15 ~ 2099-12-31",
        "method": "방문",
        "organizer": "고성읍행정복지센터 / 6705048 / 055-670-5048",
        "control": "홈페이지",
    },
    {
        "id": "101",
        "title": "종료된 강좌",
        "status": "접수마감",
        "status_class": "deadline",
        "venue": "고성문화원",
        "target": "전군민",
        "apply": "2098-01-01 ~ 2098-01-10",
        "period": "2098-02-01 ~ 2098-03-01",
        "method": "방문,전화",
        "organizer": "고성문화원 / 055-670-0000",
        "control": "홈페이지",
    },
)


def _search_form(page: int) -> str:
    return f"""
      <form name="searchForm" method="post" action="{goseong.GOSEONG_GN_LIST_PATH}">
        <input name="lectureCate" value="">
        <input name="pageIndex" value="{page}">
        <input name="pageUnit" value="8">
        <input name="pageSize" value="5">
        <input name="menuCd" value="{goseong.GOSEONG_GN_MENU}">
        <select name="lectureStatus">
          <option value="">접수상태</option><option value="1">접수대기</option>
          <option value="2">접수중</option><option value="3">접수마감</option>
        </select>
        <select name="agencyCode"><option value="">기관별</option><option value="A45">고성군</option></select>
        <select name="lectureType"><option value="">강좌분류별</option><option value="TP009">기타</option></select>
        <select name="lectureTarget"><option value="">강좌대상별</option><option value="TG005">전군민</option></select>
        <input name="lectureNm" value="">
      </form>
    """


def _navigation_form(page: int) -> str:
    return f"""
      <form name="goLecDetail" method="post" action="{goseong.GOSEONG_GN_DETAIL_PATH}">
        <input name="pageIndex" value="{page}">
        <input name="pageUnit" value="8">
        <input name="pageSize" value="5">
        <input name="menuCd" value="{goseong.GOSEONG_GN_MENU}">
        <input name="lectureSid" value="">
      </form>
    """


def _card(record: Mapping[str, str], *, title: str | None = None) -> str:
    return f"""
      <li><a class="{record['status_class']}" href="#"
        onclick="goView( {record['id']}, this)">
        <span class="state">{record['status']}</span>
        <dl><dt class="tit">{title or record['title']}</dt>
          <dd>장소 : {record['venue']}</dd>
          <dd>대상 : {record['target']}</dd>
          <dd>접수기간 : {record['apply']}</dd>
          <dd>교육기간 : {record['period']}</dd>
        </dl><span class="more"><i>상세보기</i></span>
      </a></li>
    """


def _list_html(
    page: int,
    *,
    rows: tuple[Mapping[str, str], ...] = RECORDS,
    title_override: str | None = None,
) -> str:
    cards = "".join(
        _card(row, title=title_override if index == 0 else None)
        for index, row in enumerate(rows)
    )
    return f"""
      <html><body>{_search_form(page)}
        <div class="total">총 3건 [ <span>{page}</span> / 1 페이지 ]</div>
        <div class="board-edu-list"><ul class="list-wrap">{cards}</ul></div>
        {_navigation_form(page)}
      </body></html>
    """


def _detail_html(
    record: Mapping[str, str],
    *,
    wrong_identity: bool = False,
    wrong_title: bool = False,
) -> str:
    identity = "999" if wrong_identity else record["id"]
    title = "다른 강좌" if wrong_title else record["title"]
    if record["control"] == "신청하기":
        control = (
            '<a class="bbtn type01 mg10r" href="#n" '
            'onclick="goAgree(\'2\', \'0\')"><i>신청하기</i></a>'
            '<a class="bbtn type04" href="/user/apply/list.goseong">나의신청</a>'
        )
    else:
        control = '<a class="bbtn type01 mg10r" href=""><i>홈페이지</i></a>'
    return f"""
      <html><body>
      <form name="goAgreeForm" method="post" action="/user/apply/agree.goseong">
        <input name="pageIndex" value="1"><input name="pageUnit" value="8">
        <input name="pageSize" value="5"><input name="menuCd" value="{goseong.GOSEONG_GN_MENU}">
        <input name="lectureSid" value="{identity}"><input name="lectureAppPerson" value="20">
      </form>
      <div class="board-edu-view">
        <h5 class="edu-tit {record['status_class']}">{title}</h5>
        <div class="table-wrap"><table class="type01"><tbody>
          <tr><th>장소</th><td>{record['venue']}</td><th>대상</th><td>{record['target']}</td></tr>
          <tr><th>접수기간</th><td>{record['apply']}</td><th>교육기간</th><td>{record['period']}</td></tr>
          <tr><th>이용요금</th><td>무료</td>
              <th>신청인원 / 모집인원 (대기인원)</th><td>3 / 20명 (대기인원 0명)</td></tr>
          <tr><th>예약방법</th><td>{record['method']}</td>
              <th>담당부서 / 문의전화</th><td>{record['organizer']}</td></tr>
          <tr><th>첨부파일</th><td colspan="3"></td></tr>
        </tbody></table></div>
        <div class="btn-wrap r w100p">{control}</div>
        <p class="edu-txt">담당자 개인 이름과 전화번호가 있을 수 있는 자유본문</p>
        <div class="table-wrap"><table><thead><tr>
          <th>순번</th><th>이름</th><th>연락처</th><th>접수일자</th><th>접수상태</th>
        </tr></thead><tbody><tr>
          <td>1</td><td>김OO</td><td>010-****-1234</td><td>2099-07-01</td><td>승인완료</td>
        </tr></tbody></table></div>
      </div></body></html>
    """


@dataclass
class DummySession:
    closed: bool = False

    def close(self) -> None:
        self.closed = True


class FakeSite:
    def __init__(
        self,
        *,
        bad_sentinel: bool = False,
        mutate_recheck: bool = False,
        wrong_identity: bool = False,
        wrong_title: bool = False,
        unknown_status: bool = False,
    ) -> None:
        self.bad_sentinel = bad_sentinel
        self.mutate_recheck = mutate_recheck
        self.wrong_identity = wrong_identity
        self.wrong_title = wrong_title
        self.unknown_status = unknown_status
        self.calls: Counter[tuple[str, int]] = Counter()
        self.sessions: list[DummySession] = []

    def session_factory(self) -> DummySession:
        current = DummySession()
        self.sessions.append(current)
        return current

    def fetcher(
        self,
        _session: DummySession,
        method: str,
        url: str,
        *,
        timeout: int,
        data: Mapping[str, str],
    ) -> tuple[BeautifulSoup, str]:
        assert timeout > 0
        parsed = urlparse(url)
        assert parsed.hostname == goseong.GOSEONG_GN_HOST
        if parsed.path == goseong.GOSEONG_GN_LIST_PATH:
            if method == "GET":
                assert parse_qs(parsed.query)["menuCd"] == [goseong.GOSEONG_GN_MENU]
                page = 1
            else:
                assert method == "POST"
                page = int(data["pageIndex"])
            self.calls[("list", page)] += 1
            if page == 1:
                rows = list(RECORDS)
                if self.unknown_status:
                    rows[0] = {**rows[0], "status": "임의상태", "status_class": "receipt"}
                override = (
                    "재조회 중 변경된 강좌"
                    if self.mutate_recheck and self.calls[("list", 1)] >= 2
                    else None
                )
                html = _list_html(1, rows=tuple(rows), title_override=override)
            else:
                sentinel_rows = (RECORDS[0],) if self.bad_sentinel else ()
                html = _list_html(page, rows=sentinel_rows)
            final = (
                goseong.GOSEONG_GN_URL
                if method == "GET"
                else f"https://{goseong.GOSEONG_GN_HOST}{goseong.GOSEONG_GN_LIST_PATH}"
            )
            return BeautifulSoup(html, "lxml"), final
        if parsed.path == goseong.GOSEONG_GN_DETAIL_PATH:
            assert method == "POST"
            identity = data["lectureSid"]
            record = next(item for item in RECORDS if item["id"] == identity)
            return BeautifulSoup(
                _detail_html(
                    record,
                    wrong_identity=self.wrong_identity,
                    wrong_title=self.wrong_title,
                ),
                "lxml",
            ), f"https://{goseong.GOSEONG_GN_HOST}{goseong.GOSEONG_GN_DETAIL_PATH}"
        raise AssertionError(f"unexpected {method} {url}")


def _collect(site: FakeSite, **kwargs: Any):
    return goseong.collect_gyeongnam_goseong_education_courses(
        _target(),
        timeout=5,
        max_pages=kwargs.pop("max_pages", 3),
        detail_limit=kwargs.pop("detail_limit", 2),
        session_factory=site.session_factory,
        fetcher=site.fetcher,
        today=kwargs.pop("today", "2099-07-20"),
        max_workers=1,
        **kwargs,
    )


def test_candidate_ownership_and_target_match_are_exact() -> None:
    assert set(goseong.GOSEONG_GN_CANDIDATE_IDS.values()) == {
        "MUNI_IR_902DB17450BA",
        "MUNI_IR_55CEC6D7A1DD",
        "MUNI_IR_6E0984388691",
        "MUNI_IR_CAF07F3846D3",
        "MUNI_IR_8D30AF6D294E",
    }
    assert goseong.is_gyeongnam_goseong_education_target(_target())
    reordered = (
        f"https://{goseong.GOSEONG_GN_HOST}{goseong.GOSEONG_GN_LIST_PATH}?"
        f"cpath=%2Fgsll&link=success&menuCd={goseong.GOSEONG_GN_MENU}"
    )
    assert goseong.is_gyeongnam_goseong_education_target(_target(url=reordered))
    assert not goseong.is_gyeongnam_goseong_education_target(
        _target(url=goseong.GOSEONG_GN_URL + "&lectureStatus=2")
    )
    assert not goseong.is_gyeongnam_goseong_education_target(
        _target(url=goseong.gyeongnam_goseong_detail_url("24"))
    )
    for alias in goseong.GOSEONG_GN_ALIASES:
        assert goseong.is_gyeongnam_goseong_alias_target(
            _target(provider=alias.provider, url=alias.url)
        )


def test_complete_snapshot_filters_expired_and_excludes_pii_tables() -> None:
    site = FakeSite()
    rows, parser, meta = _collect(site)

    assert parser == goseong.GOSEONG_GN_PARSER
    assert len(rows) == 2
    assert meta["declared_total"] == 3
    assert meta["source_total"] == 3
    assert meta["current_count"] == 2
    assert meta["expired_count"] == 1
    assert meta["sentinel_count"] == 0
    assert meta["stable_rechecks"] == {"1": True}
    assert meta["list_requests"] == meta["required_list_requests"] == 3
    assert meta["detail_attempts"] == meta["detail_pages"] == 2
    assert meta["detail_errors"] == 0
    assert meta["privacy_violations"] == 0
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert all(session.closed for session in site.sessions)

    by_id = {row["raw_fields"]["source_lecture_sid"]: row for row in rows}
    assert by_id["103"]["status"] == "OPEN"
    assert by_id["103"]["application_type"] == "ONLINE_RESERVATION"
    assert by_id["103"]["application_url"] == goseong.gyeongnam_goseong_detail_url("103")
    assert by_id["102"]["status"] == "CLOSED"
    assert by_id["102"]["application_type"] == "INFO_ONLY"
    assert by_id["102"]["application_url"] == ""
    assert by_id["102"]["branch"] == "고성읍행정복지센터"
    assert all(row["domain_category"] == "교육·강좌" for row in rows)
    assert all(row["raw_fields"]["applicant_table_excluded"] for row in rows)

    serialized = repr(rows)
    assert "김OO" not in serialized
    assert "010-" not in serialized
    assert "055-" not in serialized
    assert "6705048" not in serialized
    assert "자유본문" not in serialized
    assert "담당자 개인 이름" not in serialized


def test_list_and_detail_caps_fail_closed() -> None:
    rows, _parser, meta = _collect(FakeSite(), max_pages=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(), detail_limit=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]


def test_complete_archive_with_no_current_rows_is_a_valid_empty_snapshot() -> None:
    rows, _parser, meta = _collect(FakeSite(), today="2100-01-01", detail_limit=0)
    assert rows == []
    assert meta["source_total"] == 3
    assert meta["current_count"] == 0
    assert meta["detail_attempts"] == 0
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"]


def test_nonempty_sentinel_and_boundary_mutation_fail_closed() -> None:
    rows, _parser, meta = _collect(FakeSite(bad_sentinel=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sentinel" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(mutate_recheck=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "stable boundary recheck changed" in meta["configured_collection_error"]


def test_detail_identity_and_title_mismatch_fail_closed() -> None:
    rows, _parser, meta = _collect(FakeSite(wrong_identity=True))
    assert rows == []
    assert "application form lectureSid mismatch" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeSite(wrong_title=True))
    assert rows == []
    assert "list/detail title mismatch" in meta["configured_collection_error"]


def test_unknown_source_status_and_dedupe_loss_fail_closed() -> None:
    rows, _parser, meta = _collect(FakeSite(unknown_status=True))
    assert rows == []
    assert "unknown status" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(
        FakeSite(), dedupe_rows=lambda source: source[:1]
    )
    assert rows == []
    assert "dedupe changed complete row count" in meta["configured_collection_error"]


def test_managed_session_is_required_and_aliases_are_not_collected() -> None:
    rows, _parser, meta = goseong.collect_gyeongnam_goseong_education_courses(
        _target(), timeout=5, max_pages=3, detail_limit=2
    )
    assert rows == []
    assert meta["configured_collection_error"] == "managed session_factory injection is required"

    alias = goseong.GOSEONG_GN_ALIASES[2]
    site = FakeSite()
    rows, _parser, meta = goseong.collect_gyeongnam_goseong_education_courses(
        _target(provider=alias.provider, url=alias.url),
        session_factory=site.session_factory,
        fetcher=site.fetcher,
    )
    assert rows == []
    assert "canonical Gyeongnam Goseong" in meta["configured_collection_error"]
    assert site.sessions == []
