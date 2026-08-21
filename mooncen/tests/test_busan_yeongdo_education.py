from __future__ import annotations

from collections import Counter
from html import escape
import os
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import municipal_busan_yeongdo as yeongdo
from Crawler.Crawler_MunicipalYaml import CrawlTarget


class _Response:
    def __init__(self, url: str, html: str, status_code: int = 200):
        self.url = url
        self.content = html.encode("utf-8")
        self.status_code = status_code
        self.history: list[Any] = []


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target(
    provider: str = yeongdo.BUSAN_YEONGDO_PROVIDER,
    url: str = yeongdo.BUSAN_YEONGDO_CANONICAL_URL,
) -> dict[str, str]:
    return {"provider": provider, "url": url, "name": "부산 영도구 교육"}


def _root_card(
    identity: str,
    title: str,
    *,
    page: int,
    start: str,
    end: str,
    active: bool,
) -> str:
    fields = (
        ("교육기간", f"{start} ~ {end}"),
        ("교육시간", "화 10:00~12:00"),
        ("모집기간", "2099-07-01 ~ 2099-07-31"),
        ("모집대상", "영도구민"),
        ("모집인원", "0 / 20명"),
        ("접수방법", "온라인"),
    )
    values = "".join(
        f"<li>{escape(label)} : {escape(value)}</li>" for label, value in fields
    )
    route_page = f"&amp;cpage={page}" if page > 1 else ""
    if active:
        controls = f"""
          <a href="?amode=ins&amp;lecIdx={identity}{route_page}">신청하기</a>
          <a href="{yeongdo.BUSAN_YEONGDO_RESERVATION_HISTORY_PATH}">예약확인</a>
        """
    else:
        controls = '<a href="#">접수마감</a>'
    return f"""
      <li class="li1"><div class="wrap1">
        <a class="col a1" href="?amode=view&amp;idx={identity}{route_page}">
          <div class="texts"><strong class="t1">{escape(title)}</strong>
            <ul>{values}</ul></div>
        </a>
        <div class="btns">{controls}</div>
      </div></li>
    """


def _root_page(
    requested_page: int,
    *,
    drift: bool = False,
    bad_sentinel: bool = False,
) -> str:
    current_title = "[평생학습관] 미래 시민교실"
    if drift or (requested_page == 2 and bad_sentinel):
        current_title = "[평생학습관] 변경된 미래 시민교실"
    cards = _root_card(
        "101",
        current_title,
        page=requested_page,
        start="2099-08-01",
        end="2099-08-31",
        active=True,
    )
    cards += _root_card(
        "100",
        "[영도도서관] 지난 독서교실",
        page=requested_page,
        start="2098-08-01",
        end="2098-08-31",
        active=False,
    )
    return f"""
      <html><head><title>전체 | 영도구 통합예약</title></head><body>
        <div id="body_content">
          <form id="frmLecture" name="frmLecture" method="get"
            action="{yeongdo.BUSAN_YEONGDO_LIST_PATH}">
            <input name="facCode" value=""><input name="sstring" value="">
            <select name="stype"><option value="title">과정명</option></select>
          </form>
          <div class="infomenu1"><div class="info1">
            총 2건의 교육이 있습니다. ( 1 / 1 페이지 )
          </div></div>
          <div class="list1f1t2b2"><ul class="lst1">{cards}</ul></div>
          <div class="pagination"><span class="m last"><a
            title="맨끝 페이지"><i class="ic">»</i></a></span></div>
        </div>
      </body></html>
    """


def _root_detail(*, wrong_title: bool = False) -> str:
    title = (
        "[평생학습관] 다른 시민교실"
        if wrong_title
        else "[평생학습관] 미래 시민교실"
    )
    values = {
        "교육기간": "2099-08-01 ~ 2099-08-31",
        "교육시간": "화 10:00~12:00",
        "교육장소": "평생학습관 1강의실",
        "수강료": "무료",
        "준비물": "SECRET_PREPARATION private@example.test",
        "접수기간": "2099-07-01 ~ 2099-07-31",
        "모집대상": "영도구민",
        "강사": "SECRET_ROOT_INSTRUCTOR 010-1111-2222",
        "모집지역": "영도구",
        "접수방법": "온라인",
        "이용문의": "SECRET_ROOT_PHONE 051-419-0000",
        "첨부파일": "SECRET_ROOT_ATTACHMENT.hwp",
    }
    rows = "".join(
        f"<tr><th>{label}</th><td>{escape(values[label])}</td></tr>"
        for label in yeongdo._ROOT_DETAIL_LABELS
    )
    return f"""
      <html><head><title>전체 | 영도구 통합예약</title></head><body>
        <div id="body_content"><div class="view1pic1info1">
          <h1 class="h1">{escape(title)}</h1>
          <table class="t3 ttvam"><tbody>{rows}</tbody></table>
          <div>SECRET_ROOT_FREE_FORM root-private@example.test</div>
        </div></div>
      </body></html>
    """


def test_root_library_branch_is_classified_as_education_at_row_level() -> None:
    soup = BeautifulSoup(_root_page(1), "html.parser")
    rows, total, last = yeongdo._parse_root_page(soup, requested_page=1)

    assert (total, last) == (2, 1)
    library = next(row for row in rows if row["branch"] == "영도도서관")
    assert library["collection_category"] == "도서관"
    assert library["domain_category"] == "도서관"
    assert library["source_group"] == "library"
    assert library["service_group"] == "공공강좌"
    assert library["service_group_policy"] == "inferred"
    assert library["raw_fields"]["service_family"] == "education"


def test_full_generated_collection_preserves_mixed_library_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = CrawlTarget(
        provider=yeongdo.BUSAN_YEONGDO_PROVIDER,
        name="부산광역시 영도구 전체 교육 원장",
        branch="부산광역시 영도구",
        url=yeongdo.BUSAN_YEONGDO_CANONICAL_URL,
        source="test",
        priority=1,
        extra={
            "crawler_status": "ready",
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "municipality_code": yeongdo.BUSAN_YEONGDO_MUNICIPALITY_CODE,
            "municipality_full_name": yeongdo.BUSAN_YEONGDO_MUNICIPALITY_NAME,
        },
    )
    library_row = {
        "title": "영도도서관 독서교실",
        "branch": "영도도서관",
        "raw_url": f"{target.url}?amode=view&idx=100",
        "collection_category": "도서관",
        "domain_category": "도서관",
        "source_group": "library",
        "service_group": "공공강좌",
        "service_group_policy": "inferred",
    }
    ordinary_row = {
        "title": "평생학습관 시민교실",
        "branch": "평생학습관",
        "raw_url": f"{target.url}?amode=view&idx=101",
    }

    class _Writer:
        def __init__(self, _provider: str) -> None:
            pass

        def normalize_branch_split_row(self, _row: dict[str, Any]) -> None:
            pass

    monkeypatch.setattr(generated, "MunicipalDbWriter", _Writer)
    monkeypatch.setattr(
        generated,
        "collect_from_url",
        lambda *_args, **_kwargs: (
            [library_row, ordinary_row],
            "fixture",
            {
                "pages": 1,
                "detail_pages": 0,
                "pagination_complete": True,
                "recursion_depth": 0,
            },
        ),
    )

    result = generated._collect_single_target(
        target,
        per_target_limit=0,
        max_depth=1,
        max_pages=3,
        detail_limit=3,
        timeout=5,
    )

    assert result.report.success is True
    by_branch = {row["branch"]: row for row in result.rows}
    library = by_branch["영도도서관"]
    assert library["collection_category"] == "도서관"
    assert library["domain_category"] == "도서관"
    assert library["source_group"] == "library"
    assert library["service_group"] == "공공강좌"
    assert library["service_group_policy"] == "inferred"
    ordinary = by_branch["평생학습관"]
    assert ordinary["collection_category"] == "공공예약"
    assert ordinary["domain_category"] == "교육·강좌"
    assert ordinary["source_group"] == "municipal_reservation"
    assert ordinary["service_group"] == "공공강좌"
    assert ordinary["service_group_policy"] == "locked"


def _platform_row(
    sequence: int,
    *,
    identity: str,
    title: str,
    external_url: str = "",
) -> str:
    if external_url:
        title_action = f'href="{escape(external_url, quote=True)}" target="_blank"'
        action = (
            f'<a href="{escape(external_url, quote=True)}">'
            '<span class="button">수강신청</span></a>'
        )
    else:
        onclick = f"fn_learning_detail('{identity}'); return false;"
        title_action = f'href="javascript:;" onclick="{onclick}"'
        action = (
            f'<a href="javascript:;" onclick="{onclick}">'
            '<span class="button">수강신청</span></a>'
        )
    return f"""
      <tr>
        <td>{sequence}</td>
        <td class="subject"><a {title_action}>
          <span class="tit">{escape(title)}</span>
          <span class="org">영도구청</span>
        </a></td>
        <td class="type"><span>무료</span><br><span>SECRET_LIST_INSTRUCTOR</span></td>
        <td><span class="s_type blue"><em class="hidden">교육기간</em>
          2099.08.01~2099.08.31<pre>수, 14:00~16:00</pre></span></td>
        <td><span class="s_type indigo1"><em class="hidden">모집인원</em>20명</span>
          <span class="s_type red1"><em class="hidden">일반접수</em>
          2099.07.01~2099.07.31 ( 접수인원 : 3 )</span></td>
        <td><span class="s_type2 mint"><em class="hidden">선착순</em></span>
          <span class="s_btn blue">접수중</span></td>
        <td>{action}</td>
      </tr>
    """


def _platform_page(
    page: int,
    *,
    drift: bool = False,
    bad_sentinel: bool = False,
    unmatched_external: bool = False,
) -> str:
    body = ""
    if page == 1:
        native_title = "변경된 영도 인문학" if drift else "영도 인문학"
        alias_idx = "999" if unmatched_external else "101"
        external = (
            "https://www.yeongdo.go.kr/reserve/01785/01792/01793.web?"
            f"amode=ins&lecIdx={alias_idx}&facCode=001"
        )
        body = _platform_row(
            2,
            identity="LEARNING_00090001",
            title=native_title,
        )
        body += _platform_row(
            1,
            identity=external,
            title="미래 시민교실 외부연계",
            external_url=external,
        )
    elif bad_sentinel:
        external = (
            "https://www.yeongdo.go.kr/reserve/01785/01792/01793.web?"
            "amode=ins&lecIdx=101&facCode=001"
        )
        body = _platform_row(
            1,
            identity=external,
            title="경계 이탈 외부강좌",
            external_url=external,
        )
    else:
        body = '<tr><td colspan="7">등록된 교육강좌가 없습니다.</td></tr>'
    return f"""
      <html><head><title>부산평생학습플랫폼</title></head><body>
        <form id="learningVO" method="post"
          action="{yeongdo.BUSAN_LIFELONG_LIST_PATH}">
          <input name="inst_id" value="{yeongdo.BUSAN_LIFELONG_YEONGDO_OFFICE}">
          <input name="display_type" value="2"><input name="pageIndex" value="{page}">
          <input name="l_search_ch" value="0">
          <select id="o_search_ch"><option
            value="{yeongdo.BUSAN_LIFELONG_YEONGDO_OFFICE}" selected>영도구청</option></select>
          <select id="learning_state"><option value="0" selected>전체</option></select>
        </form>
        <table><thead><tr>
          <th>번호</th><th>강좌명 / 교육기관</th><th>재료비 / 강사</th>
          <th>교육기간 / 교육시간</th><th>신청기간 / 접수인원 / 대기자</th>
          <th>상태</th><th>보기</th>
        </tr></thead><tbody>{body}</tbody></table>
        <a class="page_nextend" href="?pageIndex=1"
          onclick="fn_list(1,'');return false;">마지막</a>
      </body></html>
    """


def _platform_detail(*, wrong_title: bool = False) -> str:
    title = "다른 영도 인문학" if wrong_title else "영도 인문학"
    safe_values = {
        "강좌분류": "인문교양",
        "교육대상": "부산시민",
        "교육장소": "영도구 평생학습관",
        "총 교육시간": "8시간",
        "교육기간": "2099.08.01 ~ 2099.08.31",
        "교육시간": "수, 14:00~16:00",
        "수강료": "무료",
        "재료비": "없음",
        "우선모집기간": "해당없음",
        "일반모집기간": "2099.07.01 ~ 2099.07.31",
        "모집방법": "온라인 선착순",
        "신청상태": "일반 접수중",
        "교육상태": "교육대기",
        "결제방법": "해당없음",
        "강좌제한": "없음",
    }
    skipped_values = {
        "회차명": "SECRET_SESSION",
        "문의전화": "SECRET_PLATFORM_PHONE 051-419-1234",
        "접수인원": "SECRET_ENROLLMENT 3 / 20",
        "강좌소개": "SECRET_PLATFORM_DESCRIPTION private@example.test",
        "강좌소개 첨부파일": "SECRET_PLATFORM_ATTACHMENT.hwp",
        "강사": "SECRET_PLATFORM_INSTRUCTOR 010-2222-3333",
        "강의계획서": "SECRET_PLATFORM_PLAN.pdf",
        "주의사항": "SECRET_PLATFORM_WARNING",
        "검색키워드": "SECRET_PLATFORM_KEYWORD",
    }
    definitions = []
    for label in yeongdo._PLATFORM_DETAIL_REQUIRED_LABELS:
        value = safe_values.get(label, skipped_values.get(label, ""))
        definitions.append(f"<dl><dt>{label}</dt><dd>{escape(value)}</dd></dl>")
    return f"""
      <html><head><title>부산평생학습플랫폼</title></head><body>
        <form id="learningVO" name="learningVO" method="post"
          action="{yeongdo.BUSAN_LIFELONG_DETAIL_PATH}?lng_id=LEARNING_00090001">
          <input name="inst_id" value="{yeongdo.BUSAN_LIFELONG_YEONGDO_OFFICE}">
          <input name="lng_id" value="LEARNING_00090001">
        </form>
        <h2 class="enrolTit"><span>[영도구청]</span>{escape(title)}</h2>
        <div class="form_group">{''.join(definitions)}</div>
        <a id="learning_aply_btn"
          onclick="fn_learning_apply(); return false;">일반모집신청</a>
      </body></html>
    """


def _city_card(
    *,
    title: str = "주민센터 생활요가",
    branch: str = "영도구 영선1동 주민자치회",
    source_status: str = "접수중",
    method: str = "온라인(선착순)",
) -> str:
    values = (
        ("기관", branch),
        ("대상", "제한없음"),
        ("장소", "영선1동 프로그램실"),
        (
            "일자",
            "[신청] 2099-07-01 ~ 2099-07-31 "
            "[행사] 2099-08-01 ~ 2099-08-31",
        ),
        ("방법", method),
        ("문의", "SECRET_CITY_CARD_PHONE 051-419-9999"),
    )
    definitions = "".join(
        f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in values
    )
    return f"""
      <li><a class="reserveItem" href="javascript:void(0);"
        onclick="fn_viewProgrm('157', '24935');return false;">
        <div class="infoBox"><p class="tit" title="{escape(title)}">{escape(title)}</p>
          <span class="statusMark possible">{escape(source_status)}</span><dl>{definitions}</dl>
        </div>
      </a></li>
    """


def _city_page(
    page: int,
    *,
    drift: bool = False,
    bad_sentinel: bool = False,
    wrong_owner: bool = False,
    source_status: str = "접수중",
) -> str:
    cards = ""
    if page == 1 or bad_sentinel:
        cards = _city_card(
            title="변경된 주민센터 생활요가" if drift else "주민센터 생활요가",
            branch=(
                "서구 다른동 주민자치회"
                if wrong_owner
                else "영도구 영선1동 주민자치회"
            ),
            source_status=source_status,
        )
    reserve_list = f'<ul class="reserveList">{cards}</ul>' if cards else ""
    return f"""
      <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head><body>
        <form id="srchForm" name="srchForm" method="get" action="/lctre">
          <input name="curPage" value="{page}">
          <select name="srchGugun"><option value="14" selected>영도구</option></select>
          <select name="srchResveInsttCd"><option value="33" selected>주민자치회</option></select>
        </form>
        {reserve_list}
        <div class="paginate"><a class="pgEnd"
          href="?curPage=1&amp;srchGugun=14&amp;srchResveInsttCd=33">마지막</a></div>
      </body></html>
    """


def _city_detail(
    *,
    wrong_identity: bool = False,
    source_status: str = "접수중",
    control_label: str = "예약하기",
    method: str = "온라인(선착순)",
) -> str:
    program = "99999" if wrong_identity else "24935"
    values = (
        ("운영기간", "2099-08-01(토) ~ 2099-08-31(월)"),
        ("신청기간", "2099-07-01(수) 09:00 ~ 2099-07-31(금) 18:00"),
        ("취소여부", "취소 가능"),
        ("신청방법", method),
        ("수강료", "0 원"),
        ("요일 /시간", "화 / 10:00 ~ 12:00"),
        ("문의전화", "SECRET_CITY_DETAIL_PHONE 051-419-8888"),
        ("운영기관", "영도구 영선1동 주민자치회"),
        ("대상", "제한없음"),
        ("첨부파일", "SECRET_CITY_ATTACHMENT.hwp"),
    )
    definitions = "".join(
        f"<dl><dt>{escape(label)}</dt><dd>{escape(value)}</dd></dl>"
        for label, value in values
    )
    return f"""
      <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head><body>
        <form id="viewForm" method="post">
          <input name="resveGroupSn" value="157">
          <input name="progrmSn" value="{program}">
          <div class="contHeader"><h3 class="titPage">주민센터 생활요가
            <span class="statusMark possible">{escape(source_status)}</span></h3></div>
          <div class="reserveStateWrap">
            <div class="reserveStateInfo">{definitions}</div>
            <div class="reserveBtnWrap"><a class="btnTypeXL" href="#">{escape(control_label)}</a></div>
          </div>
          <div class="reserveDetail">SECRET_CITY_FREE_FORM city-private@example.test</div>
        </form>
      </body></html>
    """


class _Backend:
    def __init__(
        self,
        *,
        bad_root_sentinel: bool = False,
        bad_platform_sentinel: bool = False,
        bad_city_sentinel: bool = False,
        root_drift: bool = False,
        platform_drift: bool = False,
        city_drift: bool = False,
        unmatched_external: bool = False,
        wrong_city_owner: bool = False,
        wrong_root_title: bool = False,
        wrong_platform_title: bool = False,
        wrong_city_identity: bool = False,
        transient_root_detail: bool = False,
    ) -> None:
        self.bad_root_sentinel = bad_root_sentinel
        self.bad_platform_sentinel = bad_platform_sentinel
        self.bad_city_sentinel = bad_city_sentinel
        self.root_drift = root_drift
        self.platform_drift = platform_drift
        self.city_drift = city_drift
        self.unmatched_external = unmatched_external
        self.wrong_city_owner = wrong_city_owner
        self.wrong_root_title = wrong_root_title
        self.wrong_platform_title = wrong_platform_title
        self.wrong_city_identity = wrong_city_identity
        self.transient_root_detail = transient_root_detail
        self.calls: Counter[str] = Counter()
        self.urls: list[str] = []
        self.timeouts: list[int] = []
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
        with self.lock:
            self.timeouts.append(timeout)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.hostname == yeongdo.BUSAN_YEONGDO_HOST:
            if parsed.path != yeongdo.BUSAN_YEONGDO_LIST_PATH:
                raise AssertionError("alias/applicant/history pages must never be fetched")
            mode = (query.get("amode") or [""])[0]
            if mode == "ins":
                raise AssertionError("Yeongdo applicant form must never be fetched")
            if mode == "view":
                identity = (query.get("idx") or [""])[0]
                count = self._record(f"root-detail-{identity}", url)
                if self.transient_root_detail and count == 1:
                    return _Response(
                        url,
                        "<html><head><title>temporary error</title></head><body>temporary</body></html>",
                    )
                return _Response(url, _root_detail(wrong_title=self.wrong_root_title))
            page = int((query.get("cpage") or ["1"])[0])
            count = self._record(f"root-list-{page}", url)
            return _Response(
                url,
                _root_page(
                    page,
                    drift=self.root_drift and page == 1 and count >= 2,
                    bad_sentinel=self.bad_root_sentinel,
                ),
            )
        if parsed.hostname == "lll.busan.go.kr":
            if parsed.path == yeongdo.BUSAN_LIFELONG_LIST_PATH:
                page = int((query.get("pageIndex") or ["1"])[0])
                count = self._record(f"platform-list-{page}", url)
                return _Response(
                    url,
                    _platform_page(
                        page,
                        drift=(
                            self.platform_drift
                            and page == 1
                            and count % 2 == 0
                        ),
                        bad_sentinel=self.bad_platform_sentinel,
                        unmatched_external=self.unmatched_external,
                    ),
                )
            if parsed.path == yeongdo.BUSAN_LIFELONG_DETAIL_PATH:
                identity = (query.get("lng_id") or [""])[0]
                self._record(f"platform-detail-{identity}", url)
                return _Response(
                    url,
                    _platform_detail(wrong_title=self.wrong_platform_title),
                )
            raise AssertionError("lifelong applicant form must never be fetched")
        if parsed.hostname == yeongdo.BUSAN_CITY_HOST:
            if parsed.path == yeongdo.BUSAN_CITY_LIST_PATH:
                page = int((query.get("curPage") or ["1"])[0])
                count = self._record(f"city-list-{page}", url)
                return _Response(
                    url,
                    _city_page(
                        page,
                        drift=self.city_drift and page == 1 and count >= 2,
                        bad_sentinel=self.bad_city_sentinel,
                        wrong_owner=self.wrong_city_owner,
                    ),
                )
            if parsed.path == yeongdo.BUSAN_CITY_DETAIL_PATH:
                self._record("city-detail", url)
                return _Response(
                    url,
                    _city_detail(wrong_identity=self.wrong_city_identity),
                )
        raise AssertionError(f"unexpected fetch {url}")


def _collect(backend: _Backend, **kwargs: Any):
    return yeongdo.collect_busan_yeongdo_education(
        _target(),
        timeout=5,
        max_pages=kwargs.pop("max_pages", 3),
        detail_limit=kwargs.pop("detail_limit", 3),
        max_requests=kwargs.pop("max_requests", 20),
        today="2099-07-20",
        fetcher=backend.fetch,
        session_factory=backend.session,
        sleeper=lambda _seconds: None,
        max_workers=1,
        **kwargs,
    )


def test_candidate_classification_owner_boundary_and_audit_are_exact() -> None:
    assert set(yeongdo.BUSAN_YEONGDO_CANDIDATE_IDS.values()) == {
        "MUNI_IR_1B09CCAFC09F",
        "MUNI_IR_AC2C4BB4CB1C",
        "MUNI_IR_C3EFB530573A",
        "MUNI_IR_C8D2702DE234",
    }
    audit = yeongdo.BUSAN_YEONGDO_OWNER_BOUNDARY_AUDIT
    assert audit[yeongdo.BUSAN_YEONGDO_PROVIDER]["decision"] == (
        "canonical_complete_district_education_owner"
    )
    assert audit[yeongdo.BUSAN_YEONGDO_NOTICE_PROVIDER]["decision"].startswith(
        "exclude_notice_board"
    )
    assert audit[yeongdo.BUSAN_CITY_YEONGDO_PROVIDER]["filter"] == {
        "srchGugun": "14",
        "srchResveInsttCd": "33",
    }
    shared = audit[yeongdo.BUSAN_LIFELONG_PROVIDER]
    assert shared["office_code"] == "OFFICE_00002680"
    assert "suppress" in shared["decision"]
    office = yeongdo._platform_office()
    assert office.ownership == "duplicate_dedicated_yeongdo_owner"
    assert office.municipality_code == ""
    assert office.municipality_name == ""
    discovery = yeongdo.BUSAN_YEONGDO_DISCOVERY_AUDIT
    assert discovery["canonical_rows"] == 2306
    assert discovery["lifelong_rows"] == 884
    assert discovery["lifelong_external_unique_idx"] == 818
    assert discovery["lifelong_external_repeated_rows"] == 0
    assert discovery["resident_rows"] == 8
    assert discovery["atomic_current_rows"] == 93


def test_target_urls_and_cross_platform_identity_are_exact() -> None:
    assert yeongdo.is_busan_yeongdo_education_target(_target())
    assert not yeongdo.is_busan_yeongdo_education_target(
        _target(url=yeongdo.BUSAN_YEONGDO_NOTICE_URL)
    )
    assert not yeongdo.is_busan_yeongdo_education_target(
        _target(url=yeongdo.BUSAN_YEONGDO_URL + "?facCode=001")
    )
    assert yeongdo.busan_yeongdo_list_url(2).endswith("?cpage=2")
    assert yeongdo.busan_yeongdo_city_list_url(3).endswith(
        "curPage=3&srchGugun=14&srchResveInsttCd=33"
    )
    platform_query = parse_qs(
        urlparse(yeongdo.busan_yeongdo_lifelong_list_url(1)).query
    )
    assert platform_query["pageUnit"] == [
        str(yeongdo.BUSAN_LIFELONG_YEONGDO_PAGE_SIZE)
    ]
    detail = yeongdo.busan_yeongdo_detail_url("101")
    alias = (
        "https://www.yeongdo.go.kr/reserve/01785/01792/01793.web?"
        "amode=ins&lecIdx=101&facCode=001"
    )
    assert yeongdo.canonical_busan_yeongdo_course_identity(detail) == "idx:101"
    assert yeongdo.canonical_busan_yeongdo_course_identity(alias) == "idx:101"
    assert yeongdo.canonical_busan_yeongdo_course_identity(alias + "&x=1") == ""
    with pytest.raises(yeongdo.BusanYeongdoContractError):
        yeongdo.busan_yeongdo_list_url(True)
    with pytest.raises(yeongdo.BusanYeongdoContractError):
        yeongdo.busan_yeongdo_city_detail_url("157", "https://evil.example")


def test_only_exact_audited_historical_reversed_ranges_are_normalized() -> None:
    assert yeongdo._root_date_range(
        "2026.01.06 ~ 2025.12.12",
        "legacy",
        identity="3359",
        kind="education",
    ) == ("2025-12-12", "2026-01-06", True)
    assert yeongdo._root_date_range(
        "2021.07.08 ~ 2021.07.01",
        "legacy",
        identity="1327",
        kind="application",
    ) == ("2021-07-01", "2021-07-08", True)
    with pytest.raises(yeongdo.BusanYeongdoContractError):
        yeongdo._root_date_range(
            "2026.01.06 ~ 2025.12.12",
            "new row",
            identity="9999",
            kind="education",
        )
    with pytest.raises(yeongdo.BusanYeongdoContractError):
        yeongdo._root_date_range(
            "2026.01.06 ~ 2025.12.11",
            "changed legacy row",
            identity="3359",
            kind="education",
        )


def test_only_idx3478_exact_target_region_dom_split_is_equivalent() -> None:
    assert yeongdo._root_target_matches(
        "3478", "영도구민 (영도구)", "영도구민", "영도구"
    ) == (True, True)
    assert yeongdo._root_target_matches(
        "3478", "영도구민 (영도구)", "영도구민", "부산광역시"
    ) == (False, False)
    assert yeongdo._root_target_matches(
        "9999", "영도구민 (영도구)", "영도구민", "영도구"
    ) == (False, False)
    assert yeongdo._root_target_matches(
        "3478", "영도구민", "영도구민", "영도구"
    ) == (True, False)


def test_city_waitlist_list_and_detail_statuses_are_equivalent() -> None:
    list_soup = BeautifulSoup(
        _city_page(1, source_status="대기접수"),
        "html.parser",
    )
    rows, last = yeongdo._parse_city_page(list_soup, page=1)

    assert last == 1
    assert len(rows) == 1
    assert rows[0]["status"] == "WAITLIST"

    detail_soup = BeautifulSoup(
        _city_detail(source_status="대기자접수", control_label="대기예약"),
        "html.parser",
    )
    detail_url = yeongdo.busan_yeongdo_city_detail_url("157", "24935")
    row = yeongdo._parse_city_detail(detail_soup, detail_url, rows[0])

    assert row["status"] == "WAITLIST"
    assert row["reservation_available"] is True
    assert row["application_type"] == "WAITLIST_APPLY"
    assert row["application_url"] == detail_url


def test_city_application_method_ignores_empty_list_separators() -> None:
    list_html = _city_page(1).replace(
        "온라인(선착순)",
        "온라인, , 방문접수, , 전화접수(선착순)",
        1,
    )
    listed, _last = yeongdo._parse_city_page(
        BeautifulSoup(list_html, "lxml"),
        page=1,
    )
    result = yeongdo._parse_city_detail(
        BeautifulSoup(
            _city_detail(
                method="온라인, 방문접수, 전화접수(선착순)",
            ),
            "lxml",
        ),
        listed[0]["raw_url"],
        listed[0],
    )

    assert result["application_method_raw"] == (
        "온라인, 방문접수, 전화접수(선착순)"
    )


def test_complete_three_ledger_snapshot_identity_suppression_and_privacy() -> None:
    backend = _Backend()
    rows, parser, meta = _collect(backend)

    assert parser == yeongdo.BUSAN_YEONGDO_PARSER
    assert [row["provider_course_id"] for row in rows] == [
        f"{yeongdo.BUSAN_YEONGDO_PROVIDER}:education:101",
        f"{yeongdo.BUSAN_YEONGDO_PROVIDER}:lifelong:LEARNING_00090001",
        f"{yeongdo.BUSAN_YEONGDO_PROVIDER}:reserve:157:24935",
    ]
    assert meta["root_source_rows"] == 2
    assert meta["platform_source_rows"] == 2
    assert meta["city_source_rows"] == 1
    assert meta["source_total"] == 5
    assert meta["unique_education_source_rows"] == 4
    assert meta["root_current_count"] == 1
    assert meta["platform_native_rows"] == 1
    assert meta["platform_native_current_count"] == 1
    assert meta["platform_external_duplicate_rows"] == 1
    assert meta["platform_external_unique_identities"] == 1
    assert meta["platform_external_repeated_rows"] == 0
    assert meta["city_current_count"] == 1
    assert meta["current_source_count"] == 3
    assert meta["expired_count"] == 1
    assert meta["required_list_requests"] == 11
    assert meta["list_requests"] == 11
    assert meta["sentinel_requests"] == 3
    assert meta["stability_rechecks"] == 5
    assert meta["detail_pages"] == 3
    assert meta["network_requests"] == 14
    assert meta["application_control_count"] == 3
    assert meta["status_counts"] == {"OPEN": 3}
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert set(backend.timeouts) == {
        yeongdo.BUSAN_YEONGDO_MIN_REQUEST_TIMEOUT_SECONDS
    }
    assert {row["raw_fields"]["source_catalog"] for row in rows} == {
        "yeongdo_complete_education_catalogue",
        "busan_lifelong_yeongdo_native",
        "busan_reserve_yeongdo_resident_councils",
    }
    assert {row["branch"] for row in rows} == {
        "평생학습관",
        "영도구청",
        "영도구 영선1동 주민자치회",
    }
    assert all(row["municipality_code"] == "2620000000" for row in rows)
    assert all(row["status"] == "OPEN" for row in rows)
    assert all(row["reservation_available"] is True for row in rows)
    assert not any("amode=ins" in url for url in backend.urls)
    assert not any(
        urlparse(url).path == yeongdo.BUSAN_YEONGDO_RESERVATION_HISTORY_PATH
        for url in backend.urls
    )

    serialized = repr(rows)
    for secret in (
        "SECRET_PREPARATION",
        "SECRET_ROOT_INSTRUCTOR",
        "SECRET_ROOT_PHONE",
        "SECRET_ROOT_ATTACHMENT",
        "SECRET_ROOT_FREE_FORM",
        "root-private@example.test",
        "SECRET_LIST_INSTRUCTOR",
        "SECRET_SESSION",
        "SECRET_PLATFORM_PHONE",
        "SECRET_ENROLLMENT",
        "SECRET_PLATFORM_DESCRIPTION",
        "SECRET_PLATFORM_ATTACHMENT",
        "SECRET_PLATFORM_INSTRUCTOR",
        "SECRET_PLATFORM_PLAN",
        "SECRET_PLATFORM_WARNING",
        "SECRET_PLATFORM_KEYWORD",
        "SECRET_CITY_CARD_PHONE",
        "SECRET_CITY_DETAIL_PHONE",
        "SECRET_CITY_ATTACHMENT",
        "SECRET_CITY_FREE_FORM",
        "city-private@example.test",
    ):
        assert secret not in serialized
    assert all(row["raw_fields"]["application_form_fetched"] is False for row in rows)


def test_transient_status_200_error_page_is_retried_atomically() -> None:
    rows, _parser, meta = _collect(_Backend(transient_root_detail=True))
    assert len(rows) == 3
    assert meta["snapshot_complete"] is True
    assert meta["network_retry_count"] == 1
    assert meta["network_requests"] == 15


@pytest.mark.parametrize(
    ("flag", "needle"),
    (
        ("bad_root_sentinel", "clamped final page"),
        ("bad_platform_sentinel", "lifelong immediate post-final sentinel"),
        ("bad_city_sentinel", "city sentinel"),
        ("root_drift", "Yeongdo boundary page changed"),
        ("platform_drift", "consecutive complete censuses changed"),
        ("city_drift", "Busan city boundary page changed"),
        ("unmatched_external", "absent from canonical idx"),
        ("wrong_city_owner", "left Yeongdo owner"),
        ("wrong_root_title", "title mismatch"),
        ("wrong_platform_title", "title mismatch"),
        ("wrong_city_identity", "detail identity changed"),
    ),
)
def test_any_source_contract_failure_discards_all_three_ledgers(
    flag: str, needle: str
) -> None:
    rows, _parser, meta = _collect(_Backend(**{flag: True}))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert needle.casefold() in meta["configured_collection_error"].casefold()


def test_caps_dedupe_and_wrong_target_fail_closed() -> None:
    rows, _parser, meta = _collect(_Backend(), max_pages=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(_Backend(), detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(_Backend(), max_requests=10)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_requests cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(
        _Backend(), dedupe_rows=lambda values: values[:2]
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]

    backend = _Backend()
    rows, _parser, meta = yeongdo.collect_busan_yeongdo_education(
        _target(url=yeongdo.BUSAN_YEONGDO_NOTICE_URL),
        fetcher=backend.fetch,
        session_factory=backend.session,
    )
    assert rows == []
    assert backend.urls == []
    assert "exact canonical" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_MUNICIPAL_AUDIT") != "1",
    reason="set RUN_LIVE_MUNICIPAL_AUDIT=1 for the 334-request live audit",
)
def test_live_complete_snapshot_matches_latest_audit_floor() -> None:
    rows, _parser, meta = yeongdo.collect_busan_yeongdo_education(
        _target(), today="2026-07-22"
    )
    assert meta["snapshot_complete"] is True
    assert meta["root_source_rows"] >= 2306
    assert meta["root_data_pages"] >= 231
    assert meta["root_current_count"] >= 45
    assert meta["platform_source_rows"] >= 884
    assert meta["platform_native_rows"] >= 66
    assert meta["platform_native_current_count"] >= 40
    assert meta["city_source_rows"] >= 8
    assert meta["city_current_count"] >= 8
    assert len(rows) == meta["current_source_count"]
