from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import municipal_geumjeong as geumjeong


def _target(
    *,
    provider: str = geumjeong.GEUMJEONG_PROVIDER,
    url: str = geumjeong.GEUMJEONG_RESERVE_URL,
) -> dict[str, str]:
    return {"provider": provider, "url": url, "name": "금정구 교육", "branch": "부산광역시 금정구"}


def _reserve_list_html(page: int, *, row: bool = True, title: str = "주민센터 요가") -> str:
    card = ""
    if row:
        card = f"""
          <ul class="reserveList"><li>
            <a class="reserveItem" href="javascript:void(0);"
               onclick="fn_viewProgrm('267', '9001');return false;">
              <span class="tit" title="{title}">{title}</span>
              <span class="statusMark possible">접수중</span>
              <dl>
                <dt>기관</dt><dd>금정구 부곡3동 주민자치회</dd>
                <dt>대상</dt><dd>제한없음</dd>
                <dt>장소</dt><dd>부곡3동 회의실</dd>
                <dt>일자</dt><dd>[신청] 2099-07-01 ~ 2099-07-31 [행사] 2099-08-01 ~ 2099-08-31</dd>
                <dt>방법</dt><dd>방문접수(선착순)</dd>
                <dt>문의</dt><dd>051-519-9999</dd>
              </dl>
            </a>
          </li></ul>
        """
    return f"""
      <html><body>
      <form id="srchForm" method="get" action="/lctre">
        <input type="hidden" name="curPage" value="{page}">
        <select name="srchGugun"><option value="2" selected>금정구</option></select>
        <select name="srchResveInsttCd"><option value="33" selected>주민자치회</option></select>
      </form>
      {card}
      <div class="paginate"><a class="pgEnd" href="?curPage=1&amp;srchGugun=2&amp;srchResveInsttCd=33">마지막 목록으로</a></div>
      </body></html>
    """


def _reserve_detail_html(
    *, wrong_identity: bool = False, method: str = "방문접수(선착순)", control: str = "방문예약"
) -> str:
    program_id = "9999" if wrong_identity else "9001"
    return f"""
      <html><body>
      <form id="viewForm" method="post">
        <input name="resveGroupSn" value="267">
        <input name="progrmSn" value="{program_id}">
      </form>
      <h3 class="titPage">주민센터 요가 <span class="statusMark possible">접수중</span></h3>
      <dl><dt>운영기간</dt><dd>2099-08-01 ~ 2099-08-31</dd></dl>
      <dl><dt>신청기간</dt><dd>2099-07-01 ~ 2099-07-31</dd></dl>
      <dl><dt>신청방법</dt><dd>{method}</dd></dl>
      <dl><dt>수강료</dt><dd>30,000 원</dd></dl>
      <dl><dt>요일 /시간</dt><dd>금 / 10:00 ~ 12:00</dd></dl>
      <dl><dt>문의전화</dt><dd>051-519-9999</dd></dl>
      <dl><dt>운영기관</dt><dd>금정구 부곡3동 주민자치회</dd></dl>
      <dl><dt>대상</dt><dd>제한없음</dd></dl>
      <a href="javascript:void(0);" onclick="return false;">{control}</a>
      </body></html>
    """


def _lifelong_directory_html() -> str:
    return f"""
      <html><body><select id="o_search_ch">
        <option value="{geumjeong.GEUMJEONG_LIFELONG_OFFICE_CODE}">금정구청</option>
      </select></body></html>
    """


def _lifelong_list_html(page: int, *, row: bool = True, title: str = "금정 인문학") -> str:
    body = "<tr><td colspan='7'>등록된 교육강좌가 없습니다.</td></tr>"
    if row:
        body = f"""
          <tr>
            <td>1</td>
            <td class="subject"><a href="javascript:;"
              onclick="fn_learning_detail('LEARNING_00990001'); return false;">
              <span class="tit">{title}</span><span class="org">금정구청</span>
            </a></td>
            <td class="type"><span>무료</span><br><span>재료비 없음</span></td>
            <td><span class="s_type blue"><em class="hidden">교육기간</em>
              2099.08.01~2099.08.31<pre>금, 14:00~16:00</pre></span></td>
            <td><span class="s_type indigo1"><em class="hidden">모집인원</em>20명</span>
              <span class="s_type red1"><em class="hidden">일반접수</em>
              2099.07.01~2099.07.31 ( 접수인원 : 3 )</span></td>
            <td><span class="s_type2 mint"><em class="hidden">선착순</em></span>
              <span class="s_btn blue">접수중</span></td>
            <td><a href="javascript:;" onclick="fn_learning_detail('LEARNING_00990001'); return false;">
              <span class="button">수강신청</span></a></td>
          </tr>
        """
    return f"""
      <html><body>
      <form id="learningVO" method="post" action="{geumjeong.busan_lifelong.BUSAN_LIFELONG_LIST_PATH}">
        <input name="inst_id" value="{geumjeong.GEUMJEONG_LIFELONG_OFFICE_CODE}">
        <input name="display_type" value="2">
        <input name="pageIndex" value="{page}">
        <input name="l_search_ch" value="0">
        <select id="o_search_ch"><option value="{geumjeong.GEUMJEONG_LIFELONG_OFFICE_CODE}" selected>금정구청</option></select>
        <select id="learning_state"><option value="0" selected>전체</option></select>
      </form>
      <table><thead><tr>
        <th>번호</th><th>강좌명 / 교육기관</th><th>재료비 / 강사</th>
        <th>교육기간 / 교육시간</th><th>신청기간 / 접수인원 / 대기자</th>
        <th>상태</th><th>보기</th>
      </tr></thead><tbody>{body}</tbody></table>
      <a class="page_nextend" href="?pageIndex=1" onclick="fn_list(1,'');return false;">마지막</a>
      </body></html>
    """


def _lifelong_detail_html(*, wrong_identity: bool = False) -> str:
    identity = "LEARNING_00999999" if wrong_identity else "LEARNING_00990001"
    return f"""
      <html><body><form id="learningVO" method="post">
        <input name="inst_id" value="{geumjeong.GEUMJEONG_LIFELONG_OFFICE_CODE}">
        <input name="lng_id" value="{identity}">
      </form>
      <h2 class="enrolTit"><span>[금정구청]</span>금정 인문학</h2>
      <div class="form_group"><dl><dt>교육기간</dt><dd>2099.08.01 ~ 2099.08.31</dd></dl></div>
      <div class="form_group"><dl><dt>일반모집기간</dt><dd>2099.07.01 ~ 2099.07.31</dd></dl></div>
      <div class="form_group"><dl><dt>교육대상</dt><dd>부산시민</dd></dl></div>
      <div class="form_group"><dl><dt>문의전화</dt><dd>051-519-8888</dd></dl></div>
      <div class="form_group"><dl><dt>교육장소</dt><dd>금정구 평생학습관</dd></dl></div>
      <div class="form_group"><dl><dt>수강료</dt><dd>무료</dd></dl></div>
      <div class="form_group"><dl><dt>강사</dt><dd>개인 강사명</dd></dl></div>
      <div class="form_group"><dl><dt>신청상태</dt><dd>접수중</dd></dl></div>
      <a id="learning_aply_btn" onclick="fn_learning_apply(); return false;">일반모집신청</a>
      </body></html>
    """


@dataclass
class FakeSession:
    closed: bool = False

    def close(self) -> None:
        self.closed = True


class FakeBackend:
    def __init__(
        self,
        *,
        bad_reserve_sentinel: bool = False,
        mutate_reserve_recheck: bool = False,
        wrong_reserve_identity: bool = False,
        wrong_lifelong_identity: bool = False,
    ) -> None:
        self.bad_reserve_sentinel = bad_reserve_sentinel
        self.mutate_reserve_recheck = mutate_reserve_recheck
        self.wrong_reserve_identity = wrong_reserve_identity
        self.wrong_lifelong_identity = wrong_lifelong_identity
        self.calls: Counter[tuple[str, int]] = Counter()

    def session(self) -> FakeSession:
        return FakeSession()

    def fetch(
        self,
        _session: FakeSession,
        method: str,
        url: str,
        *,
        timeout: int,
        data: Mapping[str, str],
    ) -> tuple[BeautifulSoup, str]:
        assert timeout > 0
        parsed = urlparse(url)
        if parsed.hostname == geumjeong.GEUMJEONG_RESERVE_HOST:
            if parsed.path == geumjeong.GEUMJEONG_RESERVE_PATH:
                assert method == "GET"
                page = int((parse_qs(parsed.query).get("curPage") or ["1"])[0])
                self.calls[("reserve", page)] += 1
                title = (
                    "변경된 주민센터 요가"
                    if self.mutate_reserve_recheck
                    and page == 1
                    and self.calls[("reserve", page)] >= 2
                    else "주민센터 요가"
                )
                include = page == 1 or (page == 2 and self.bad_reserve_sentinel)
                return BeautifulSoup(
                    _reserve_list_html(page, row=include, title=title), "lxml"
                ), url
            if parsed.path == geumjeong.GEUMJEONG_RESERVE_DETAIL_PATH:
                assert method == "GET"
                return BeautifulSoup(
                    _reserve_detail_html(wrong_identity=self.wrong_reserve_identity),
                    "lxml",
                ), url
        if parsed.hostname == geumjeong.GEUMJEONG_LIFELONG_HOST:
            if parsed.path == geumjeong.busan_lifelong.BUSAN_LIFELONG_OFFICE_PATH:
                assert method == "GET"
                return BeautifulSoup(_lifelong_directory_html(), "lxml"), url
            if parsed.path == geumjeong.busan_lifelong.BUSAN_LIFELONG_LIST_PATH:
                assert method == "POST"
                page = int(data["pageIndex"])
                self.calls[("lifelong", page)] += 1
                return BeautifulSoup(
                    _lifelong_list_html(page, row=page == 1), "lxml"
                ), url
            if parsed.path == geumjeong.busan_lifelong.BUSAN_LIFELONG_DETAIL_PATH:
                assert method == "POST"
                return BeautifulSoup(
                    _lifelong_detail_html(wrong_identity=self.wrong_lifelong_identity),
                    "lxml",
                ), url
        raise AssertionError(f"unexpected {method} {url}")


def _collect(backend: FakeBackend, **kwargs: Any):
    return geumjeong.collect_geumjeong_education_courses(
        _target(),
        timeout=5,
        max_pages=kwargs.pop("max_pages", 7),
        detail_limit=kwargs.pop("detail_limit", 2),
        session_factory=backend.session,
        fetcher=backend.fetch,
        today="2099-07-20",
        max_workers=1,
        **kwargs,
    )


def test_candidate_and_target_ownership_is_exact() -> None:
    assert set(geumjeong.GEUMJEONG_CANDIDATE_IDS.values()) == {
        "MUNI_IR_4332B8F8A6D7",
        "MUNI_IR_1F10B5A64EC7",
        "MUNI_IR_25B29F5C6E7D",
        "MUNI_IR_D3561ECC97DC",
        "MUNI_IR_9B610BD87527",
    }
    assert geumjeong.is_geumjeong_education_target(_target())
    assert geumjeong.is_geumjeong_education_target(
        _target(url=geumjeong.GEUMJEONG_RESERVE_URL.replace("?", "?&"))
    )
    assert not geumjeong.is_geumjeong_education_target(
        _target(url=geumjeong.GEUMJEONG_RESERVE_URL + "&srchCtgry=1")
    )
    assert not geumjeong.is_geumjeong_education_target(
        _target(url="https://reserve.busan.go.kr/exprn/list?srchGugun=2")
    )
    assert not geumjeong.is_geumjeong_education_target(
        _target(provider="MUNI_WWW_GEUMJEONG_GO_KR_C5590860")
    )
    local_alias = geumjeong.GEUMJEONG_OWNERSHIP_ALIASES[1]
    assert geumjeong.is_geumjeong_owned_alias_target(
        _target(provider=local_alias.provider, url=local_alias.url)
    )
    federation = geumjeong.GEUMJEONG_OWNERSHIP_ALIASES[0]
    assert not geumjeong.is_geumjeong_owned_alias_target(
        _target(provider=federation.provider, url=federation.url)
    )


def test_complete_two_source_snapshot_and_pii_allowlist() -> None:
    rows, parser, meta = _collect(FakeBackend())

    assert parser == geumjeong.GEUMJEONG_PARSER
    assert len(rows) == 2
    assert meta["source_totals"] == {
        "busan_reserve": 1,
        "busan_lifelong_geumjeong": 1,
    }
    assert meta["source_current_counts"] == {
        "busan_reserve": 1,
        "busan_lifelong_geumjeong": 1,
    }
    assert meta["sentinel_counts"] == {
        "busan_reserve": 0,
        "busan_lifelong_geumjeong": 0,
    }
    assert meta["stable_recheck_count"] == 2
    assert meta["detail_attempts"] == 2
    assert meta["detail_pages"] == 2
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert len({row["provider_course_id"] for row in rows}) == 2
    assert {row["raw_fields"]["source_catalog"] for row in rows} == {
        "busan_reserve_geumjeong_resident_centres",
        "busan_lifelong_geumjeong_office",
    }
    reserve = next(row for row in rows if ":reserve:" in row["provider_course_id"])
    lifelong = next(row for row in rows if ":lifelong:" in row["provider_course_id"])
    assert reserve["application_type"] == "OFFLINE_APPLY"
    assert reserve["application_url"] == ""
    assert lifelong["application_type"] == "ONLINE_RESERVATION"
    assert lifelong["application_url"].startswith("https://lll.busan.go.kr/")
    serialized = repr(rows)
    assert "051-519-" not in serialized
    assert "개인 강사명" not in serialized
    assert "@" not in serialized
    assert all(row["domain_category"] == "교육·강좌" for row in rows)


def test_live_style_phone_reception_is_an_explicit_offline_control() -> None:
    parent_rows, errors = geumjeong._reserve_list_rows(
        BeautifulSoup(_reserve_list_html(1), "lxml"), page=1
    )
    assert errors == []
    parent_rows[0]["raw_fields"]["source_application_method"] = "전화접수"
    row, errors = geumjeong._reserve_detail_row(
        parent_rows[0],
        BeautifulSoup(
            _reserve_detail_html(method="전화접수", control="전화접수"), "lxml"
        ),
    )
    assert errors == []
    assert row["application_type"] == "OFFLINE_APPLY"
    assert row["application_url"] == ""
    assert row["reservation_available"] is False


def test_page_and_detail_caps_fail_closed() -> None:
    rows, _parser, meta = _collect(FakeBackend(), max_pages=6)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeBackend(), detail_limit=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]


def test_nonempty_sentinel_and_boundary_mutation_fail_closed() -> None:
    rows, _parser, meta = _collect(FakeBackend(bad_reserve_sentinel=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sentinel" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeBackend(mutate_reserve_recheck=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "stable boundary recheck changed" in meta["configured_collection_error"]


def test_detail_identity_mismatch_fails_the_combined_snapshot() -> None:
    rows, _parser, meta = _collect(FakeBackend(wrong_reserve_identity=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "detail program identity mismatch" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FakeBackend(wrong_lifelong_identity=True))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "detail identity mismatch" in meta["configured_collection_error"]


def test_managed_session_is_required_and_alias_pages_are_not_collected() -> None:
    rows, _parser, meta = geumjeong.collect_geumjeong_education_courses(
        _target(), timeout=5, max_pages=7, detail_limit=2
    )
    assert rows == []
    assert meta["configured_collection_error"] == "managed session_factory injection is required"

    alias = geumjeong.GEUMJEONG_OWNERSHIP_ALIASES[2]
    rows, _parser, meta = geumjeong.collect_geumjeong_education_courses(
        _target(provider=alias.provider, url=alias.url),
        session_factory=FakeBackend().session,
        fetcher=FakeBackend().fetch,
    )
    assert rows == []
    assert "canonical Geumjeong education route" in meta["configured_collection_error"]
