from __future__ import annotations

from collections import Counter
from html import escape
import os
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_busan_yeonje as yeonje


class _Response:
    def __init__(self, url: str, html: str, status_code: int = 200):
        self.url = url
        self.content = html.encode("utf-8")
        self.status_code = status_code
        self.history: list[Any] = []


class _Session:
    def close(self) -> None:
        pass


def _target(
    provider: str = yeonje.BUSAN_YEONJE_PROVIDER,
    url: str = yeonje.BUSAN_YEONJE_CANONICAL_URL,
) -> dict[str, str]:
    return {"provider": provider, "url": url, "name": "연제구 교육"}


_LOCAL_ROWS = (
    {
        "identity": "9001",
        "title": "미래 시민교실",
        "start": "2099-08-01",
        "end": "2099-08-31",
        "status": "접수중",
        "branch": "연제구청(평생교육과)",
        "method": "온라인접수",
    },
    {
        "identity": "9000",
        "title": "지난 시민교실",
        "start": "2098-08-01",
        "end": "2098-08-31",
        "status": "교육마감",
        "branch": "연제구청(평생교육과)",
        "method": "온라인접수",
    },
)


def _keyset(identity: str, page: int) -> str:
    return "{'lecIdx': '%s','page': '%s','searchType': '','searchTxt': ''}" % (
        identity,
        page,
    )


def _local_card(row: dict[str, str], page: int) -> str:
    values = (
        ("접수기간", "2099-07-01 ~ 2099-07-31"),
        ("모집/신청", "20명 / 3명"),
        ("학습기간", f"{row['start']} ~ {row['end']}"),
        ("교육기관", row["branch"]),
        ("접수방법", row["method"]),
        ("상   태", row["status"]),
    )
    definitions = "".join(
        f"<li><dl><dt>{escape(label)}</dt><dd>{escape(value)}</dd></dl></li>"
        for label, value in values
    )
    controls = ""
    if row["status"] == "접수중":
        controls = f"""
          <div class="taC">
            <a href="#" data-action="write.do" data-keyset="{escape(_keyset(row['identity'], page), quote=True)}"
              onclick="req.post(this); return false;">신청하기</a>
            <a href="#" data-action="view.do" data-keyset="{escape(_keyset(row['identity'], page), quote=True)}"
              onclick="req.post(this); return false;">자세히 보기</a>
          </div>
        """
    return f"""
      <div class="edu_items cB">
        <div class="cB"><p class="fl lecture_tit">No. {row['identity']}
          <a href="#" data-action="view.do" data-keyset="{escape(_keyset(row['identity'], page), quote=True)}"
            onclick="req.post(this); return false;"><span>{escape(row['title'])}</span></a>
        </p></div>
        <ul class="lecture_ul">{definitions}</ul>{controls}
      </div>
    """


def _local_page(
    page: int, *, bad_sentinel: bool = False, drift: bool = False
) -> str:
    rows = [dict(row) for row in _LOCAL_ROWS]
    if drift:
        rows[0]["title"] = "변경된 미래 시민교실"
    cards = ""
    if page == 1 or bad_sentinel:
        cards = "".join(_local_card(row, page) for row in rows)
    pagination = ""
    if page == 1 or bad_sentinel:
        pagination = (
            '<div class="bod_page"><a class="on"><b>1</b></a>'
            '<a class="btn_end" href="#" onclick="goPage(1); return false;">맨끝</a></div>'
        )
    return f"""
      <html><body>
        <form name="list" method="post" action="?mId={yeonje.BUSAN_YEONJE_MENU}">
          <input name="page" value="{page}"><input name="lecIdx" value="0">
          <input name="searchAccept" value=""><input name="searchTitle" value="">
        </form>
        <div class="lecture_wrap">{cards}</div>{pagination}
      </body></html>
    """


def _local_detail(*, wrong_title: bool = False) -> str:
    title = "다른 시민교실" if wrong_title else "미래 시민교실"
    values = {
        "사업명": "인문교양",
        "학습기관": "연제구청(평생교육과)",
        "학습기간": "2099-08-01 ~ 2099-08-31",
        "접수기간": "2099-07-01 ~ 2099-07-31",
        "교육시간": "수 14:00~16:00",
        "강사명": "SECRET_LOCAL_INSTRUCTOR 010-1111-2222",
        "수강료": "무료",
        "추가비용": "0",
        "교육방법": "오프라인",
        "교육대상": "연제구민",
        "교육주기": "매주",
        "교육정원": "20명",
        "교육장소": "연제구 평생학습관",
        "교육문의전화": "SECRET_LOCAL_PHONE 051-111-2222",
        "접수방법": "온라인접수",
        "상태": "접수중",
        "직업능력개발훈련비지원": "미지원",
        "학점은행제평가(학점)인증": "미인증",
        "평생학습계좌제평가인증": "미지원",
        "언어": "ko",
        "시각장애지원": "미지원",
        "청각장애지원": "미지원",
        "신청서": "SECRET_LOCAL_ATTACHMENT private@example.test",
        "기타파일": "SECRET_LOCAL_OTHER_FILE",
    }
    rows = [f'<tr><th colspan="4">{escape(title)}</th></tr>']
    for label in yeonje._LOCAL_DETAIL_LABELS:
        rows.append(
            f"<tr><th>{escape(label)}</th><td>{escape(values[label])}</td></tr>"
        )
    rows.insert(-2, '<tr><td colspan="4"><div class="pad10a">SECRET_LOCAL_FREE_FORM 010-9999-9999</div></td></tr>')
    return f"""
      <html><body>
        <form name="list" method="post" action="list.do?mId={yeonje.BUSAN_YEONJE_MENU}">
          <input name="lecIdx" value="9001"><input name="page" value="1">
        </form>
        <table class="tbl Thead"><tbody>{''.join(rows)}</tbody></table>
        <a href="#" data-action="write.do" data-keyset="{escape(_keyset('9001', 1), quote=True)}"
          onclick="req.post(this); return false;">신청하기</a>
        <a href="#" data-action="list.do">목록</a>
        <table class="tbl taC"><thead><tr>
          <th>번호</th><th>이름</th><th>연락처</th><th>신청일</th><th>비고</th>
        </tr></thead><tbody><tr><td>1</td><td>SECRET_APPLICANT</td>
          <td>010-3333-4444</td><td>2099-01-01</td><td>private@example.test</td></tr></tbody></table>
      </body></html>
    """


def _platform_row(
    sequence: int,
    *,
    kind: str,
    identity: str,
    title: str,
    start: str,
    end: str,
    status: str,
) -> str:
    if kind == "external":
        action = (
            f'href="https://www.yeonje.go.kr/edu/lecture/view.do?mId='
            f'{yeonje.BUSAN_YEONJE_EXTERNAL_MENU}&amp;lecIdx={identity}"'
        )
    elif kind == "internal":
        action = f'href="#" onclick="fn_learning_detail(\'{identity}\')"'
    else:
        action = ""
    return f"""
      <tr>
        <td>{sequence}</td>
        <td class="subject"><a {action}>
          <span class="tit">{escape(title)}</span><span class="org">연제구청</span>
        </a></td>
        <td><span>무료</span><span>SECRET_PLATFORM_INSTRUCTOR 010-2222-3333</span></td>
        <td><span class="s_type blue"><em class="hidden">교육기간</em>
          {start}~{end}<pre>수 14:00~16:00</pre></span></td>
        <td><span class="s_type indigo1"><em class="hidden">모집인원</em>20명</span>
          <span class="s_type red1"><em class="hidden">일반접수</em>
          2099-07-01~2099-07-31 (접수인원 SECRET_ENROLLMENT)</span></td>
        <td><span class="s_type2 mint"><em class="hidden">선착순</em></span>
          <span class="s_btn blue">{status}</span></td><td><a>보기</a></td>
      </tr>
    """


def _platform_page(
    office_code: str,
    page: int,
    *,
    bad_sentinel: bool = False,
    unmatched_external: bool = False,
    drift: bool = False,
) -> str:
    name = dict(yeonje.BUSAN_LIFELONG_YEONJE_OFFICES)[office_code]
    body = '<tr><td colspan="7">등록된 교육강좌가 없습니다.</td></tr>'
    if office_code == "OFFICE_00002670" and (page == 1 or bad_sentinel):
        current_title = "변경된 미래 시민교실" if drift else "미래 시민교실"
        body = "".join(
            (
                _platform_row(
                    3,
                    kind="external",
                    identity="9999" if unmatched_external else "9001",
                    title=current_title,
                    start="2099-08-01",
                    end="2099-08-31",
                    status="접수중",
                ),
                _platform_row(
                    2,
                    kind="list_only",
                    identity="",
                    title="지난 시민교실",
                    start="2098-08-01",
                    end="2098-08-31",
                    status="교육완료",
                ),
                _platform_row(
                    1,
                    kind="internal",
                    identity="LEARNING_00090001",
                    title="플랫폼 미래교실",
                    start="2099-08-01",
                    end="2099-08-31",
                    status="접수중",
                ),
            )
        )
    return f"""
      <html><body>
        <form id="learningVO" method="post" action="{yeonje.BUSAN_LIFELONG_LIST_PATH}">
          <input name="inst_id" value="{office_code}"><input name="display_type" value="2">
          <input name="pageIndex" value="{page}"><input name="pageUnit" value="1000">
          <input name="l_search_ch" value="0">
          <select id="o_search_ch"><option value="{office_code}" selected>{escape(name)}</option></select>
          <select id="learning_state"><option value="0" selected>전체</option></select>
        </form>
        <table><thead><tr><th>번호</th><th>강좌명 / 교육기관</th><th>재료비 / 강사</th>
          <th>교육기간 / 교육시간</th><th>신청기간 / 접수인원</th><th>상태</th><th>보기</th>
        </tr></thead><tbody>{body}</tbody></table>
        <a class="page_nextend" href="?pageIndex=1" onclick="fn_list(1,'');return false;">마지막</a>
      </body></html>
    """


def _platform_detail() -> str:
    safe_values = {
        "교육대상": "연제구민",
        "교육장소": "연제구 평생학습관",
        "교육기간": "2099-08-01 ~ 2099-08-31",
        "교육시간": "수 14:00~16:00",
        "수강료": "무료",
        "일반모집기간": "2099-07-01 ~ 2099-07-31",
        "모집방법": "온라인 선착순",
        "신청상태": "접수중",
    }
    private_values = {
        "문의전화": "SECRET_PLATFORM_PHONE 051-222-3333",
        "접수인원": "SECRET_PLATFORM_ENROLMENT",
        "강좌소개": "SECRET_PLATFORM_FREE_FORM private@example.test",
        "강좌소개 첨부파일": "SECRET_PLATFORM_ATTACHMENT",
        "강사": "SECRET_PLATFORM_TEACHER 010-4444-5555",
        "강의계획서": "SECRET_PLATFORM_PLAN",
    }
    definitions = "".join(
        f"<dl><dt>{escape(label)}</dt><dd>{escape(safe_values.get(label, private_values.get(label, '미지원')))}</dd></dl>"
        for label in yeonje._PLATFORM_DETAIL_REQUIRED_LABELS
    )
    return f"""
      <html><body><form id="learningVO" name="learningVO" method="post"
        action="{yeonje.BUSAN_LIFELONG_DETAIL_PATH}?lng_id=LEARNING_00090001">
        <input name="lng_id" value="LEARNING_00090001">
        <input name="inst_id" value="OFFICE_00002670">
      </form>
      <h2 class="enrolTit"><span>[연제구청]</span>플랫폼 미래교실</h2>
      <div class="form_group">{definitions}</div>
      <a id="learning_aply_btn" onclick="fn_learning_apply(); return false;">일반모집신청</a>
      </body></html>
    """


def _city_card() -> str:
    values = (
        ("기관", "연제구 거제4동 주민자치회"),
        ("대상", "제한없음"),
        ("장소", "거제4동 프로그램실"),
        ("일자", "[신청] 2099-07-01 ~ 2099-07-31 [행사] 2099-08-01 ~ 2099-08-31"),
        ("방법", "방문접수"),
        ("문의", "SECRET_CITY_CARD_PHONE 051-800-9999"),
    )
    definitions = "".join(
        f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in values
    )
    return f"""
      <li><a class="reserveItem" onclick="fn_viewProgrm('288', '11253');return false;">
        <div class="infoBox"><p class="tit" title="주민센터 생활요가">주민센터 생활요가</p>
          <span class="statusMark expired">접수마감</span><dl>{definitions}</dl></div>
      </a></li>
    """


def _city_page(page: int, *, bad_sentinel: bool = False, drift: bool = False) -> str:
    reserve = ""
    if page == 1 or bad_sentinel:
        card = _city_card()
        if drift:
            card = card.replace("주민센터 생활요가", "변경된 주민센터 생활요가")
        reserve = f'<ul class="reserveList">{card}</ul>'
    return f"""
      <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head><body>
        <form id="srchForm" name="srchForm" method="get" action="/lctre">
          <input name="curPage" value="{page}">
          <select name="srchGugun"><option value="13" selected>연제구</option></select>
          <select name="srchResveInsttCd"><option value="33" selected>주민자치회</option></select>
        </form>{reserve}
        <div class="paginate"><a class="pgEnd"
          href="?curPage=1&amp;srchGugun=13&amp;srchResveInsttCd=33">마지막</a></div>
      </body></html>
    """


def _city_detail() -> str:
    values = (
        ("운영기간", "2099-08-01(토) ~ 2099-08-31(월)"),
        ("신청기간", "2099-07-01(수) 09:00 ~ 2099-07-31(금) 18:00"),
        ("취소여부", "취소 가능"),
        ("신청방법", "방문접수"),
        ("수강료", "0 원"),
        ("요일 /시간", "수 / 14:00 ~ 16:00"),
        ("문의전화", "SECRET_CITY_PHONE 051-800-8888"),
        ("운영기관", "연제구 거제4동 주민자치회"),
        ("대상", "제한없음"),
        ("첨부파일", "SECRET_CITY_ATTACHMENT private@example.test"),
    )
    definitions = "".join(
        f"<dl><dt>{escape(label)}</dt><dd>{escape(value)}</dd></dl>"
        for label, value in values
    )
    return f"""
      <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head><body>
        <form id="viewForm" method="post">
          <input name="resveGroupSn" value="288"><input name="progrmSn" value="11253">
          <div class="contHeader"><h3 class="titPage">주민센터 생활요가
            <span class="statusMark expired">접수마감</span></h3></div>
          <div class="reserveStateWrap"><div class="reserveStateInfo">{definitions}</div>
            <div class="reserveBtnWrap"><a class="btnTypeXL btnColorType3">접수마감</a></div>
          </div><div class="reserveDetail">SECRET_CITY_FREE_FORM city@example.test</div>
        </form>
      </body></html>
    """


class _Backend:
    def __init__(
        self,
        *,
        bad_local_sentinel: bool = False,
        bad_platform_sentinel: bool = False,
        bad_city_sentinel: bool = False,
        local_drift: bool = False,
        platform_drift: bool = False,
        city_drift: bool = False,
        unmatched_external: bool = False,
        wrong_local_title: bool = False,
    ) -> None:
        self.bad_local_sentinel = bad_local_sentinel
        self.bad_platform_sentinel = bad_platform_sentinel
        self.bad_city_sentinel = bad_city_sentinel
        self.local_drift = local_drift
        self.platform_drift = platform_drift
        self.city_drift = city_drift
        self.unmatched_external = unmatched_external
        self.wrong_local_title = wrong_local_title
        self.calls: Counter[str] = Counter()
        self.urls: list[str] = []
        self.lock = Lock()

    def session(self) -> _Session:
        return _Session()

    def _record(self, key: str, url: str) -> int:
        with self.lock:
            self.calls[key] += 1
            self.urls.append(url)
            return self.calls[key]

    def fetch(self, _session: _Session, url: str, timeout: int) -> _Response:
        assert timeout > 0
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.hostname == yeonje.BUSAN_YEONJE_HOST:
            if parsed.path == yeonje.BUSAN_YEONJE_LIST_PATH:
                page = int((query.get("page") or ["1"])[0])
                count = self._record(f"local-list-{page}", url)
                return _Response(
                    url,
                    _local_page(
                        page,
                        bad_sentinel=self.bad_local_sentinel,
                        drift=self.local_drift and page == 1 and count >= 2,
                    ),
                )
            if parsed.path == yeonje.BUSAN_YEONJE_DETAIL_PATH:
                self._record("local-detail", url)
                return _Response(url, _local_detail(wrong_title=self.wrong_local_title))
        if parsed.hostname == "lll.busan.go.kr":
            if parsed.path == yeonje.BUSAN_LIFELONG_LIST_PATH:
                office = (query.get("inst_id") or [""])[0]
                page = int((query.get("pageIndex") or ["1"])[0])
                count = self._record(f"platform-{office}-{page}", url)
                return _Response(
                    url,
                    _platform_page(
                        office,
                        page,
                        bad_sentinel=self.bad_platform_sentinel,
                        unmatched_external=self.unmatched_external,
                        drift=(
                            self.platform_drift
                            and office == "OFFICE_00002670"
                            and page == 1
                            and count >= 2
                        ),
                    ),
                )
            if parsed.path == yeonje.BUSAN_LIFELONG_DETAIL_PATH:
                self._record("platform-detail", url)
                return _Response(url, _platform_detail())
        if parsed.hostname == yeonje.BUSAN_CITY_HOST:
            if parsed.path == yeonje.BUSAN_CITY_LIST_PATH:
                page = int((query.get("curPage") or ["1"])[0])
                count = self._record(f"city-list-{page}", url)
                return _Response(
                    url,
                    _city_page(
                        page,
                        bad_sentinel=self.bad_city_sentinel,
                        drift=self.city_drift and page == 1 and count >= 2,
                    ),
                )
            if parsed.path == yeonje.BUSAN_CITY_DETAIL_PATH:
                self._record("city-detail", url)
                return _Response(url, _city_detail())
        raise AssertionError(f"unexpected fetch {url}")


def _collect(backend: _Backend, **kwargs: Any):
    return yeonje.collect_busan_yeonje_education(
        _target(),
        timeout=5,
        max_pages=kwargs.pop("max_pages", 30),
        detail_limit=kwargs.pop("detail_limit", 5),
        max_requests=kwargs.pop("max_requests", 40),
        today="2099-07-20",
        fetcher=backend.fetch,
        session_factory=backend.session,
        sleeper=lambda _seconds: None,
        max_workers=1,
        **kwargs,
    )


def test_provider_candidates_owner_boundaries_and_audit_are_exact() -> None:
    assert yeonje.BUSAN_YEONJE_PROVIDER == "MUNI_WWW_YEONJE_GO_KR_73BA35A2"
    assert yeonje.BUSAN_CITY_YEONJE_PROVIDER == "MUNI_RESERVE_BUSAN_GO_KR_6976F0A8"
    assert set(yeonje.BUSAN_YEONJE_CANDIDATE_IDS.values()) == {
        "MUNI_IR_B24708E62D19",
        "MUNI_IR_36E54AD8BE14",
        "MUNI_IR_42AFB37AE6EB",
        "MUNI_IR_4332B8F8A6D7",
        "MUNI_IR_F3E36EF468AA",
        "MUNI_IR_B8064EE21FAE",
    }
    assert yeonje.BUSAN_YEONJE_OWNER_BOUNDARY_AUDIT[yeonje.BUSAN_CITY_YEONJE_PROVIDER][
        "filter"
    ] == {"srchGugun": "13", "srchResveInsttCd": "33"}
    assert tuple(office.code for office in yeonje._platform_offices()) == (
        "OFFICE_00002670",
        "OFFICE_00002760",
        "OFFICE_00002910",
        "OFFICE_00002770",
    )
    audit = yeonje.BUSAN_YEONJE_DISCOVERY_AUDIT
    assert audit["district_rows"] == 415
    assert audit["lifelong_rows"] == 605
    assert audit["duplicate_platform_rows"] == 415
    assert audit["resident_rows"] == 39
    assert audit["atomic_current_rows"] == 132
    assert audit["complete_network_requests"] == 196


def test_target_and_url_identity_contracts_are_exact() -> None:
    assert yeonje.is_busan_yeonje_education_target(_target())
    assert yeonje.is_busan_yeonje_education_target(
        _target(url=yeonje.BUSAN_YEONJE_REGISTERED_URL)
    )
    assert not yeonje.is_busan_yeonje_education_target(
        _target(url=yeonje.BUSAN_YEONJE_CANONICAL_URL + "&search=요가")
    )
    assert yeonje.busan_yeonje_list_url(2).endswith("mId=0701010000&page=2")
    assert yeonje.busan_yeonje_city_list_url(3).endswith(
        "curPage=3&srchGugun=13&srchResveInsttCd=33"
    )
    platform = parse_qs(
        urlparse(yeonje.busan_yeonje_lifelong_list_url("OFFICE_00002670", 1)).query
    )
    assert platform["pageUnit"] == ["1000"]
    detail = yeonje.busan_yeonje_detail_url("9001", 2)
    assert yeonje.canonical_busan_yeonje_course_identity(detail) == "lecIdx:9001"
    assert yeonje.canonical_busan_yeonje_course_identity(detail + "&x=1") == ""
    with pytest.raises(yeonje.BusanYeonjeContractError):
        yeonje.busan_yeonje_lifelong_list_url("OFFICE_EVIL", 1)


def test_complete_atomic_snapshot_duplicate_suppression_and_privacy() -> None:
    backend = _Backend()
    rows, parser, meta = _collect(backend)
    assert parser == yeonje.BUSAN_YEONJE_PARSER
    assert [row["provider_course_id"] for row in rows] == [
        f"{yeonje.BUSAN_YEONJE_PROVIDER}:district:9001",
        f"{yeonje.BUSAN_YEONJE_PROVIDER}:lifelong:LEARNING_00090001",
        f"{yeonje.BUSAN_YEONJE_PROVIDER}:reserve:288:11253",
    ]
    assert meta["district_source_rows"] == 2
    assert meta["platform_source_rows"] == 3
    assert meta["platform_external_duplicate_rows"] == 1
    assert meta["platform_list_only_duplicate_rows"] == 1
    assert meta["platform_native_rows"] == 1
    assert meta["platform_rows_by_office"] == {
        "OFFICE_00002670": 3,
        "OFFICE_00002760": 0,
        "OFFICE_00002910": 0,
        "OFFICE_00002770": 0,
    }
    assert meta["city_source_rows"] == 1
    assert meta["source_total"] == 6
    assert meta["unique_education_source_rows"] == 4
    assert meta["current_source_count"] == 3
    assert meta["expired_count"] == 1
    assert meta["required_list_requests"] == 18
    assert meta["list_requests"] == 18
    assert meta["sentinel_requests"] == 6
    assert meta["stability_rechecks"] == 6
    assert meta["detail_pages"] == 3
    assert meta["network_requests"] == 21
    assert meta["application_control_count"] == 2
    assert meta["status_counts"] == {"CLOSED": 1, "OPEN": 2}
    assert meta["returned_count"] == 3
    assert meta["snapshot_complete"] is True
    assert meta["atomic_union_complete"] is True
    assert all(row["municipality_code"] == "2647000000" for row in rows)
    assert rows[0]["reservation_available"] is True
    assert rows[1]["reservation_available"] is True
    assert rows[2]["application_type"] == "INFO_ONLY"
    assert not any(yeonje.BUSAN_YEONJE_APPLY_PATH in url for url in backend.urls)

    serialized = repr(rows)
    for secret in (
        "SECRET_LOCAL_INSTRUCTOR",
        "SECRET_LOCAL_PHONE",
        "SECRET_LOCAL_ATTACHMENT",
        "SECRET_LOCAL_OTHER_FILE",
        "SECRET_LOCAL_FREE_FORM",
        "SECRET_APPLICANT",
        "SECRET_PLATFORM_INSTRUCTOR",
        "SECRET_ENROLLMENT",
        "SECRET_PLATFORM_PHONE",
        "SECRET_PLATFORM_ENROLMENT",
        "SECRET_PLATFORM_FREE_FORM",
        "SECRET_PLATFORM_ATTACHMENT",
        "SECRET_PLATFORM_TEACHER",
        "SECRET_PLATFORM_PLAN",
        "SECRET_CITY_CARD_PHONE",
        "SECRET_CITY_PHONE",
        "SECRET_CITY_ATTACHMENT",
        "SECRET_CITY_FREE_FORM",
        "private@example.test",
        "city@example.test",
        "010-1111-2222",
        "010-2222-3333",
        "010-3333-4444",
        "010-4444-5555",
        "051-800-9999",
        "051-800-8888",
    ):
        assert secret not in serialized


@pytest.mark.parametrize(
    "flag",
    (
        "bad_local_sentinel",
        "bad_platform_sentinel",
        "bad_city_sentinel",
        "local_drift",
        "platform_drift",
        "city_drift",
        "unmatched_external",
        "wrong_local_title",
    ),
)
def test_any_contract_drift_discards_the_atomic_union(flag: str) -> None:
    rows, _, meta = _collect(_Backend(**{flag: True}))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["atomic_union_complete"] is False
    assert meta["configured_collection_error"]


def test_caps_dedupe_and_wrong_target_fail_closed() -> None:
    for kwargs in (
        {"max_pages": 17},
        {"detail_limit": 2},
        {"max_requests": 20},
    ):
        rows, _, meta = _collect(_Backend(), **kwargs)
        assert rows == []
        assert meta["source_cap_reached"] is True
        assert meta["configured_collection_error"]

    rows, _, meta = _collect(_Backend(), dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed atomic row count" in meta["configured_collection_error"]

    backend = _Backend()
    rows, _, meta = yeonje.collect_busan_yeonje_education(
        _target(provider="WRONG_PROVIDER"), fetcher=backend.fetch
    )
    assert rows == []
    assert backend.urls == []
    assert "target does not match" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_MUNICIPAL_AUDIT") != "1",
    reason="exact live municipal audit is opt-in",
)
def test_live_complete_three_ledger_snapshot_is_exact() -> None:
    rows, parser, meta = yeonje.collect_busan_yeonje_education(
        _target(),
        timeout=60,
        max_pages=100,
        detail_limit=200,
        max_requests=300,
        today="2026-07-22",
        max_workers=12,
    )
    assert parser == yeonje.BUSAN_YEONJE_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["district_source_rows"] == 415
    assert meta["district_data_pages"] == 42
    assert meta["district_current_count"] == 16
    assert meta["platform_source_rows"] == 605
    assert meta["platform_external_rows"] == 366
    assert meta["platform_list_only_rows"] == 49
    assert meta["platform_external_duplicate_rows"] == 366
    assert meta["platform_list_only_duplicate_rows"] == 49
    assert meta["platform_native_rows"] == 190
    assert meta["platform_native_current_count"] == 77
    assert meta["city_source_rows"] == 39
    assert meta["city_data_pages"] == 4
    assert meta["city_current_count"] == 39
    assert meta["source_total"] == 1059
    assert meta["unique_education_source_rows"] == 644
    assert meta["current_source_count"] == 132
    assert meta["returned_count"] == 132
    assert meta["required_list_requests"] == 64
    assert meta["detail_pages"] == 132
    assert meta["network_requests"] == 196
    assert meta["application_control_count"] == 15
    assert meta["status_counts"] == {"CLOSED": 117, "OPEN": 15}
    assert len(rows) == 132
    assert len({row["provider_course_id"] for row in rows}) == 132
    assert all(row["municipality_code"] == "2647000000" for row in rows)
