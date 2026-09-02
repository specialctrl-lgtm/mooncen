from __future__ import annotations

from collections import Counter
from html import escape
import os
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_busan_seogu as seogu


class _Response:
    def __init__(self, url: str, html: str, status_code: int = 200):
        self.url = url
        self.content = html.encode("utf-8")
        self.status_code = status_code
        self.history: list[Any] = []
        self.headers = {"Content-Type": "text/html; charset=UTF-8"}


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target(
    provider: str = seogu.BUSAN_SEOGU_PROVIDER,
    url: str = seogu.BUSAN_SEOGU_URL,
) -> dict[str, str]:
    return {"provider": provider, "url": url, "name": "부산 서구 교육"}


def _local_row(
    identity: str,
    source_title: str,
    *,
    status: str,
    start: str,
    end: str,
) -> str:
    status_class = "btn-ing" if status == "접수중" else "btn-end"
    return f"""
      <tr class="lecture-name">
        <td class="l"><a href="#" onclick="view_flag('{identity}')">
          <span class="btxt">{escape(source_title)}</span>
          <span class="stxt">접수기간 : 2099-07-01 ~ 2099-07-31</span>
          <span class="stxt">교육기간 : {start} ~ {end}</span>
          <span class="stxt">교육장소 : 서구평생학습관</span>
        </a></td>
        <td>30</td><td>일반성인</td>
        <td><span class="break">온라인접수</span>
          <span class="break">전화접수</span></td>
        <td>무료</td><td><span class="{status_class}">{status}</span>
          <div id="timetest">SECRET_SERVER_CLOCK</div></td>
      </tr>
    """


def _local_page(
    page: int,
    *,
    drift: bool = False,
    nonempty_sentinel: bool = False,
) -> str:
    rows = ""
    if page == 1:
        title = "1. 변경된 미래 인문학" if drift else "1. 미래 인문학"
        rows = _local_row(
            "9001",
            title,
            status="접수중",
            start="2099-08-01",
            end="2099-08-31",
        )
        rows += _local_row(
            "9000",
            "2. 지난 인문학",
            status="접수마감",
            start="2099-05-01",
            end="2099-05-31",
        )
    elif nonempty_sentinel:
        rows = _local_row(
            "9999",
            "경계 이탈 강좌",
            status="접수마감",
            start="2099-05-01",
            end="2099-05-31",
        )
    else:
        rows = '<tr><td colspan="6">등록된 데이터가 없습니다.</td></tr>'
    headers = "".join(f"<th>{item}</th>" for item in seogu._LOCAL_HEADERS)
    return f"""
      <html><head><title>{seogu._LOCAL_TITLE}</title></head><body>
        <div id="contents">
          <div class="board-top"><div class="total">
            총 <span>2</span>건의 게시물이 있습니다
            ( <span>{page}/1</span> 페이지 )
          </div>
          <form class="rfc_bbs_searchForm" method="get"
            action="/edu/index.bsseogu">
            <input name="nowPage" value="1">
            <input name="searchType" value="A.ECB_NAME">
            <input name="el_code" value="">
            <input name="menuCd" value="{seogu.BUSAN_SEOGU_LIST_MENU}">
            <input name="keyword" value="">
          </form></div>
          <div class="board-list-wrap lecture-list"><table>
            <thead><tr>{headers}</tr></thead><tbody>{rows}</tbody>
          </table></div>
        </div>
      </body></html>
    """


def _local_detail(
    *,
    wrong_title: bool = False,
    changed_pii_label: bool = False,
) -> str:
    title = "1. 다른 제목" if wrong_title else "1. 미래 인문학"
    fields = (
        ("강좌명", title),
        ("학습기관", "서구평생학습관"),
        ("학습기간", "2099-08-01 ~ 2099-08-31"),
        ("접수기간", "2099-07-01 ~ 2099-07-31"),
        ("교육시간", "화 10:00~12:00"),
        ("수강료", "무료"),
        (
            "강사개인정보" if changed_pii_label else "강사정보",
            "SECRET_INSTRUCTOR_010-1111-2222",
        ),
        ("교육대상", "일반성인"),
        ("교육주기", ""),
        ("교육정원 / 대기정원", "30 / 0"),
        ("교육장소", "서구평생학습관"),
        ("교육문의전화", "SECRET_PHONE_051-240-4044"),
        ("접수방법", "온라인접수 전화접수"),
        ("상태", "접수중"),
        ("상세내용", "SECRET_FREE_FORM private@example.com"),
        ("첨부파일", "SECRET_ATTACHMENT_WITH_PERSON_NAME.hwp"),
    )
    table_rows = "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>"
        for label, value in fields
    )
    application = seogu.busan_seogu_application_url("9001")
    return f"""
      <html><head><title>{seogu._LOCAL_TITLE}</title></head><body>
        <div id="contents"><table><tbody>{table_rows}</tbody></table>
          <a href="{escape(application)}" onclick="check_back();">신청하기</a>
          <form name="rfc_bbs_searchForm" method="get"
            action="/reserve/index.bsseogu">
            <input name="el_code" value="9001">
            <input name="menuCd" value="{seogu.BUSAN_SEOGU_LIST_MENU}">
          </form>
        </div>
      </body></html>
    """


def _chi_row(ledger: seogu._ChiLedger, *, drift: bool = False) -> str:
    if ledger.key == "women":
        identity = "501"
        prefix = "[여성센터]"
        title = "변경된 미래 여성교육" if drift else "미래 여성교육"
        identity_attrs = f'href="#" onclick="view_flag(\'{identity}\')"'
    elif ledger.key == "happy":
        identity = "601"
        prefix = "[프로그램]"
        title = "변경된 꿈키움 교실" if drift else "꿈키움 교실"
        identity_attrs = f'href="javascript:goDetailPage(\'{identity}\')"'
    else:
        identity = "701"
        prefix = "[작은도서관 프로그램]"
        title = "지난 도서관 교실"
        identity_attrs = f'href="#" onclick="view_flag(\'{identity}\')"'
    start, end = (
        ("2099-05-01", "2099-05-31")
        if ledger.key == "library"
        else ("2099-08-01", "2099-08-31")
    )
    return f"""
      <li><a {identity_attrs}>
        <span class="state end">접수마감</span>
        <span class="btxt"><span class="green">{prefix}</span>{title}</span>
        <span class="ctxt"><span>접수기간</span>2099-07-01 ~ 2099-07-31</span>
        <span class="ctxt"><span>교육기간</span>{start} ~ {end}</span>
        <span class="ctxt"><span>모집인원</span>20명</span>
        <span class="ctxt"><span>접수방법</span>온라인접수</span>
      </a><span class="btn-link end">접수 마감되었습니다.</span></li>
    """


def _chi_page(
    ledger: seogu._ChiLedger,
    page: int,
    *,
    nonempty_sentinel: bool = False,
    drift: bool = False,
) -> str:
    if page == 1 or nonempty_sentinel:
        items = _chi_row(ledger, drift=drift)
    elif ledger.sentinel_kind == "marker_li":
        items = "<li>등록된 데이터가 없습니다.</li>"
    else:
        items = ""
    return f"""
      <html><head><title>{seogu._CHI_TITLE}</title></head><body>
        <div id="contents">
          <form class="rfc_bbs_searchForm" method="get"
            action="/reserve/index.bsseogu">
            <input name="nowPage" value="1"><input name="nowBlock" value="0">
            <input name="searchType" value="{ledger.search_type}">
            <input name="{ledger.identity_param}" value="">
            <input name="menuCd" value="{ledger.list_menu}">
            <input name="el_sdate" value=""><input name="el_edate" value="">
            <input name="keyword" value=""><select name="el_r_flag"></select>
          </form>
          <div class="courseList-wrap">
            <div class="total"><p>총 <span>1</span>건의 게시물이 있습니다
              ( <span>{page}/1</span> 페이지 )</p></div>
            <ul>{items}</ul>
          </div>
        </div>
      </body></html>
    """


def _chi_detail_form(ledger: seogu._ChiLedger, identity: str) -> str:
    action_menu = (
        ledger.list_menu
        if ledger.key == "women"
        else "DOM_000001001001000000"
    )
    hidden_menu = (
        "DOM_000001001001000000"
        if ledger.key == "library"
        else ledger.list_menu
    )
    search_type = "" if ledger.key == "women" else ledger.search_type
    return f"""
      <form name="rfc_bbs_searchForm" method="get"
        action="/reserve/index.bsseogu?menuCd={action_menu}">
        <input name="nowPage" value="1"><input name="nowBlock" value="0">
        <input name="{ledger.identity_param}" value="{identity}">
        <input name="el_sdate" value=""><input name="el_edate" value="">
        <input name="searchType" value="{search_type}">
        <input name="keyword" value=""><input name="menuCd" value="{hidden_menu}">
    """


def _chi_women_detail(*, changed_pii_label: bool = False) -> str:
    fields = (
        ("교육과정", "미래 여성교육"),
        ("교육기간", "2099-08-01 ~ 2099-08-31"),
        ("교육시간", "화 10:00 ~ 12:00"),
        ("수강신청기간", "2099-07-01(09시) ~ 2099-07-31(18시)"),
        ("교육정원", "20 명"),
        ("교육장소", "서구여성센터 강의실"),
        ("마감여부", "마감"),
    )
    safe_rows = "".join(
        f"<tr><th><span>{label}</span></th><td>{value}</td></tr>"
        for label, value in fields
    )
    pii_header = "개인전화" if changed_pii_label else "연락처"
    return f"""
      <html><head><title>{seogu._CHI_TITLE}</title></head><body>
        <div id="contents">{_chi_detail_form(seogu._CHI_LEDGERS[0], '501')}
          <div class="board-write-wrap"><table><tbody>{safe_rows}</tbody></table>
            <table class="tbl-type01"><thead><tr><th>번호</th><th>이름</th>
              <th>{pii_header}</th><th>신청일</th></tr></thead><tbody>
              <tr><td>1</td><td>SECRET_APPLICANT_NAME</td>
                <td>SECRET_APPLICANT_PHONE</td><td>SECRET_APPLY_TIME</td></tr>
            </tbody></table>
          </div>
        </form></div>
      </body></html>
    """


def _chi_happy_detail(*, changed_pii_label: bool = False) -> str:
    fields = (
        ("모집인원(대기인원)", "20명(5명)"),
        ("신청인원", "22명"),
        ("교육기간", "2099-08-01 ~ 2099-08-31"),
        ("접수기간", "2099-07-01 (10:00) ~ 2099-07-31 (18:00)"),
        ("교육주기", "주 1회"),
        ("교육시간/요일", "화 10:00 ~ 12:00"),
        ("학습기관", "부산글로벌빌리지"),
        ("수강료", "0"),
        ("교육분야", "희망교육"),
        ("교육방법", "오프라인"),
        ("교육장소", "부산글로벌빌리지"),
        ("교육대상", ""),
        ("접수처", "SECRET_APPLICATION_OFFICE"),
        ("개인전화" if changed_pii_label else "문의전화", "SECRET_HAPPY_PHONE"),
        ("접수방법", "온라인접수"),
        ("상태", "접수마감"),
        ("강의내용", "SECRET_HAPPY_FREE_FORM"),
        ("강사명", "SECRET_HAPPY_INSTRUCTOR"),
        ("강사 학력 정보", "SECRET_HAPPY_ACADEMIC"),
        ("강사 자격증", "SECRET_HAPPY_CERTIFICATE"),
        ("강사 강의경력", "SECRET_HAPPY_CAREER"),
        ("비고", "SECRET_HAPPY_NOTES"),
        ("첨부파일", "SECRET_HAPPY_ATTACHMENT"),
    )
    rows = []
    for label, value in fields:
        rows.append(f"<tr><th>{label}</th><td colspan='3'>{value}</td></tr>")
    return f"""
      <html><head><title>{seogu._CHI_TITLE}</title></head><body>
        <div id="contents">{_chi_detail_form(seogu._CHI_LEDGERS[1], '601')}
          <div class="board-view-wrap02"><table><tbody>
            <tr><td colspan="4">꿈키움 교실</td></tr>{''.join(rows)}
          </tbody></table></div>
          <p class="tc btnwarp"><a class="bg-btn"
            href="javascript:check_back();"><span class="apply">신청하기</span></a></p>
          <!-- SECRET_BACKEND_BEAN_PHONE_AND_FREE_FORM -->
        </form></div>
      </body></html>
    """


def _city_card(
    *,
    title: str = "주민센터 미술교실",
    branch: str = "서구 서대신4동 주민자치회",
) -> str:
    return f"""
      <li><a class="reserveItem" href="javascript:void(0);"
        onclick="fn_viewProgrm('77', '8001');return false;">
        <div class="infoBox">
          <p class="tit" title="{escape(title)}">{escape(title)}</p>
          <span class="statusMark possible">접수중</span>
          <dl>
            <dt>기관</dt><dd>{escape(branch)}</dd>
            <dt>대상</dt><dd>제한없음</dd>
            <dt>장소</dt><dd>서대신4동 프로그램실</dd>
            <dt>일자</dt><dd>[신청] 2099-07-01 ~ 2099-07-31
              [행사] 2099-08-01 ~ 2099-08-31</dd>
            <dt>방법</dt><dd>방문접수, 전화접수</dd>
            <dt>문의</dt><dd>SECRET_CARD_PHONE_051-240-9999</dd>
          </dl>
        </div>
      </a></li>
    """


def _city_page(
    page: int,
    *,
    drift: bool = False,
    nonempty_sentinel: bool = False,
    wrong_owner: bool = False,
) -> str:
    cards = ""
    if page == 1 or nonempty_sentinel:
        cards = _city_card(
            title="변경된 주민센터 미술교실" if drift else "주민센터 미술교실",
            branch=(
                "중구 다른동 주민자치회"
                if wrong_owner
                else "서구 서대신4동 주민자치회"
            ),
        )
    result = (
        f'<ul class="reserveList">{cards}</ul>'
        if cards
        else '<div class="reserveListWrap"><div class="txtCenter">'
        "등록된 강좌가 없습니다.</div></div>"
    )
    return f"""
      <html><head><title>{seogu._CITY_LIST_TITLE}</title></head><body>
        <form id="srchForm" method="get" action="/lctre">
          <input name="curPage" value="{page}">
          <select name="srchGugun"><option value="11" selected>서구</option></select>
          <select name="srchResveInsttCd"><option value="33" selected>
            주민자치회</option></select>
        </form>
        <div class="reserveListType">{result}
          <div class="paginate"><a class="pgEnd"
            href="?curPage=1&amp;srchGugun=11&amp;srchResveInsttCd=33">
            마지막 목록으로</a></div>
        </div>
      </body></html>
    """


def _city_detail(*, wrong_identity: bool = False) -> str:
    program = "8999" if wrong_identity else "8001"
    values = (
        ("운영기간", "2099-08-01(토) ~ 2099-08-31(월)"),
        ("신청기간", "2099-07-01(수) 09:00 ~ 2099-07-31(금) 18:00"),
        ("취소여부", "취소 가능"),
        ("신청방법", "방문접수, 전화접수"),
        ("수강료", "0 원"),
        ("요일 /시간", "화 / 10:00 ~ 12:00"),
        ("문의전화", "SECRET_DETAIL_PHONE_051-240-8888"),
        ("운영기관", "서구 서대신4동 주민자치회"),
        ("대상", "제한없음"),
    )
    definitions = "".join(
        f"<dl><dt>{escape(label)}</dt><dd>{escape(value)}</dd></dl>"
        for label, value in values
    )
    return f"""
      <html><head><title>{seogu._CITY_LIST_TITLE}</title></head><body>
        <form id="viewForm" method="post">
          <input name="resveGroupSn" value="77">
          <input name="progrmSn" value="{program}">
          <div class="contHeader"><h3 class="titPage">주민센터 미술교실
            <span class="titState"><span class="statusMark possible">
              접수중</span></span></h3></div>
          <div class="reserveStateWrap"><div class="reserveState">
            <div class="reserveStateInfo">{definitions}
              <div class="reserveBtnWrap"></div>
            </div>
          </div></div>
          <div class="reserveDetail">
            SECRET_CITY_FREE_FORM city-private@example.com
            SECRET_ADDRESS SECRET_INSTRUCTOR
          </div>
        </form>
      </body></html>
    """


class _Backend:
    def __init__(
        self,
        *,
        bad_local_sentinel: bool = False,
        bad_chi_sentinel: bool = False,
        bad_city_sentinel: bool = False,
        local_drift: bool = False,
        chi_drift: bool = False,
        city_drift: bool = False,
        wrong_city_owner: bool = False,
        wrong_city_identity: bool = False,
        wrong_local_title: bool = False,
        changed_pii_label: bool = False,
        changed_happy_pii_label: bool = False,
        transient_local_detail: bool = False,
    ) -> None:
        self.bad_local_sentinel = bad_local_sentinel
        self.bad_chi_sentinel = bad_chi_sentinel
        self.bad_city_sentinel = bad_city_sentinel
        self.local_drift = local_drift
        self.chi_drift = chi_drift
        self.city_drift = city_drift
        self.wrong_city_owner = wrong_city_owner
        self.wrong_city_identity = wrong_city_identity
        self.wrong_local_title = wrong_local_title
        self.changed_pii_label = changed_pii_label
        self.changed_happy_pii_label = changed_happy_pii_label
        self.transient_local_detail = transient_local_detail
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
        if parsed.hostname == seogu.BUSAN_SEOGU_HOST:
            menu = (query.get("menuCd") or [""])[0]
            if parsed.path == seogu.BUSAN_SEOGU_PATH and menu == seogu.BUSAN_SEOGU_LIST_MENU:
                page = int((query.get("nowPage") or ["1"])[0])
                count = self._record(f"local-list-{page}", url)
                return _Response(
                    url,
                    _local_page(
                        page,
                        drift=self.local_drift and page == 1 and count >= 2,
                        nonempty_sentinel=self.bad_local_sentinel and page == 2,
                    ),
                )
            if parsed.path == seogu.BUSAN_SEOGU_PATH and menu == seogu.BUSAN_SEOGU_DETAIL_MENU:
                identity = (query.get("el_code") or [""])[0]
                count = self._record(f"local-detail-{identity}", url)
                if self.transient_local_detail and count == 1:
                    return _Response(
                        url,
                        "<html><head><title>temporary error</title></head></html>",
                    )
                return _Response(
                    url,
                    _local_detail(
                        wrong_title=self.wrong_local_title,
                        changed_pii_label=self.changed_pii_label,
                    ),
                )
            if parsed.path == seogu.BUSAN_SEOGU_CHI_PATH:
                list_ledger = seogu._CHI_LEDGER_BY_LIST_MENU.get(menu)
                if list_ledger is not None:
                    page = int((query.get("nowPage") or ["1"])[0])
                    count = self._record(
                        f"chi-{list_ledger.key}-list-{page}", url
                    )
                    return _Response(
                        url,
                        _chi_page(
                            list_ledger,
                            page,
                            nonempty_sentinel=(
                                self.bad_chi_sentinel
                                and list_ledger.key == "women"
                                and page == 2
                            ),
                            drift=(
                                self.chi_drift
                                and list_ledger.key == "women"
                                and page == 1
                                and count >= 2
                            ),
                        ),
                    )
                detail_ledger = seogu._CHI_LEDGER_BY_DETAIL_MENU.get(menu)
                if detail_ledger is not None:
                    identity = (query.get(detail_ledger.identity_param) or [""])[0]
                    self._record(
                        f"chi-{detail_ledger.key}-detail-{identity}", url
                    )
                    if detail_ledger.key == "women":
                        return _Response(url, _chi_women_detail())
                    if detail_ledger.key == "happy":
                        return _Response(
                            url,
                            _chi_happy_detail(
                                changed_pii_label=self.changed_happy_pii_label
                            ),
                        )
                    raise AssertionError(
                        "expired library detail must not be fetched"
                    )
            raise AssertionError("login/private/applicant pages must never be fetched")
        if parsed.hostname == seogu.BUSAN_CITY_HOST:
            if parsed.path == seogu.BUSAN_CITY_LIST_PATH:
                page = int((query.get("curPage") or ["1"])[0])
                count = self._record(f"city-list-{page}", url)
                return _Response(
                    url,
                    _city_page(
                        page,
                        drift=self.city_drift and page == 1 and count >= 2,
                        nonempty_sentinel=self.bad_city_sentinel and page == 2,
                        wrong_owner=self.wrong_city_owner,
                    ),
                )
            if parsed.path == seogu.BUSAN_CITY_DETAIL_PATH:
                self._record("city-detail", url)
                return _Response(
                    url,
                    _city_detail(wrong_identity=self.wrong_city_identity),
                )
        raise AssertionError(f"unexpected fetch {url}")


def _collect(backend: _Backend, **kwargs: Any):
    return seogu.collect_busan_seogu_education(
        _target(),
        timeout=5,
        max_pages=kwargs.pop("max_pages", 5),
        detail_limit=kwargs.pop("detail_limit", 4),
        max_requests=kwargs.pop("max_requests", 30),
        today="2099-07-20",
        fetcher=backend.fetch,
        session_factory=backend.session,
        sleeper=lambda _seconds: None,
        max_workers=1,
        **kwargs,
    )


def test_candidate_classification_and_duplicate_ownership_are_exact() -> None:
    assert set(seogu.BUSAN_SEOGU_CANDIDATE_IDS.values()) == {
        "MUNI_IR_3E1A5287FA4F",
        "MUNI_IR_4332B8F8A6D7",
        "MUNI_IR_AFD656D8AD0E",
        "MUNI_IR_F6065FC650FB",
    }
    audit = seogu.BUSAN_SEOGU_OWNER_BOUNDARY_AUDIT
    assert audit[seogu.BUSAN_SEOGU_PROVIDER]["decision"] == (
        "canonical_district_education_owner"
    )
    assert audit[seogu.BUSAN_SEOGU_REDIRECT_PROVIDER]["decision"] == (
        "render_alias_of_canonical_edu_ledger"
    )
    assert audit[seogu.BUSAN_SEOGU_PRIVATE_HISTORY_PROVIDER]["decision"].endswith(
        "never_fetch"
    )
    shared = audit[seogu.BUSAN_LIFELONG_PROVIDER]
    assert shared["office_code"] == "OFFICE_00002641"
    assert shared["observed_rows"] == shared["same_el_code_identity_rows"] == 20
    assert "suppress" in shared["decision"]
    assert audit["OFFICIAL_BUSAN_CITY_RESERVATION"]["filter"] == {
        "srchGugun": "11",
        "srchResveInsttCd": "33",
    }
    assert audit["BUSAN_CITY_DETAIL_384_24458"]["operator"].startswith("서구 ")
    public_chi = audit["BUSAN_SEOGU_CHI_PUBLIC_EDUCATION"]
    assert set(public_chi["menus"]) == {"women", "happy", "library"}
    assert public_chi["private_menu_never_fetch"] == "DOM_000001006001000000"
    digital = audit["BUSAN_SEOGU_CHI_DIGITAL_ARCHIVE"]
    assert digital["decision"] == "exclude_broken_identityless_archive"
    assert digital["declared_rows"] == 224
    assert digital["bindable_local_identities"] == 0


def test_target_url_and_identity_contracts_are_exact() -> None:
    assert seogu.is_busan_seogu_education_target(_target())
    assert not seogu.is_busan_seogu_education_target(
        _target(url=seogu.busan_seogu_list_url(1))
    )
    assert not seogu.is_busan_seogu_education_target(
        _target(provider=seogu.BUSAN_SEOGU_REDIRECT_PROVIDER)
    )
    assert seogu.busan_seogu_list_url(2).endswith(
        "menuCd=DOM_000000703001001000&nowPage=2"
    )
    assert seogu.busan_seogu_detail_url("1945").endswith(
        "menuCd=DOM_000000703001004000&el_code=1945"
    )
    assert seogu.busan_seogu_chi_list_url("women", 11).endswith(
        "menuCd=DOM_000001001009000000&nowPage=11"
    )
    assert seogu.busan_seogu_chi_detail_url("women", "139").endswith(
        "menuCd=DOM_000001001009001000&edu_id=139"
    )
    assert seogu.busan_seogu_chi_detail_url("happy", "70").endswith(
        "menuCd=DOM_000001001010001000&el_code=70"
    )
    assert seogu.busan_seogu_chi_detail_url("library", "10").endswith(
        "menuCd=DOM_000001001011001000&el_code=10"
    )
    assert seogu.busan_seogu_city_list_url(3).endswith(
        "curPage=3&srchGugun=11&srchResveInsttCd=33"
    )
    assert seogu.busan_seogu_city_detail_url("384", "24458").endswith(
        "resveGroupSn=384&progrmSn=24458"
    )
    with pytest.raises(seogu.BusanSeoguContractError):
        seogu.busan_seogu_list_url(True)
    with pytest.raises(seogu.BusanSeoguContractError):
        seogu.busan_seogu_city_detail_url("384", "https://evil.example")
    with pytest.raises(seogu.BusanSeoguContractError):
        seogu.busan_seogu_chi_list_url("private", 1)
    dedicated = seogu.busan_seogu_detail_url("1945")
    assert seogu.canonical_busan_seogu_course_identity(dedicated) == (
        "bsseogu:1945"
    )
    assert seogu.canonical_busan_seogu_course_identity(
        dedicated + "&unexpected=1"
    ) == ""


def test_complete_atomic_snapshot_application_identity_and_pii_boundaries() -> None:
    backend = _Backend()
    rows, parser, meta = _collect(backend)

    assert parser == seogu.BUSAN_SEOGU_PARSER
    assert [row["provider_course_id"] for row in rows] == [
        "edu:9001",
        "women:501",
        "happy:601",
        "reserve:77:8001",
    ]
    assert meta["source_rows"] == 6
    assert meta["district_source_rows"] == 2
    assert meta["chi_source_rows"] == 3
    assert meta["women_source_rows"] == 1
    assert meta["happy_source_rows"] == 1
    assert meta["library_source_rows"] == 1
    assert meta["city_source_rows"] == 1
    assert meta["current_source_count"] == 4
    assert meta["district_current_count"] == 1
    assert meta["chi_current_count"] == 2
    assert meta["women_current_count"] == 1
    assert meta["happy_current_count"] == 1
    assert meta["library_current_count"] == 0
    assert meta["city_current_count"] == 1
    assert meta["expired_count"] == 2
    assert meta["list_requests"] == 20
    assert meta["required_list_requests"] == 20
    assert meta["sentinel_requests"] == 5
    assert meta["stability_rechecks"] == 10
    assert meta["detail_pages"] == 4
    assert meta["network_requests"] == 24
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert meta["application_control_count"] == 1
    assert meta["offline_application_count"] == 1

    local, women, happy, city = rows
    assert local["title"] == "미래 인문학"
    assert local["branch"] == "서구평생학습관"
    assert local["status"] == "OPEN"
    assert local["application_url"] == seogu.busan_seogu_application_url("9001")
    assert local["application_type"] == "LOGIN_REQUIRED"
    assert women["branch"] == "서구여성센터"
    assert women["status"] == "CLOSED"
    assert women["application_url"] == ""
    assert women["raw_fields"]["applicant_table_values_never_read"] is True
    assert happy["branch"] == "부산글로벌빌리지"
    assert happy["status"] == "CLOSED"
    assert happy["application_url"] == ""
    assert happy["raw_fields"]["closed_application_control_ignored"] is True
    assert city["branch"] == "서구 서대신4동 주민자치회"
    assert city["status"] == "OPEN"
    assert city["application_url"] == ""
    assert city["application_type"] == "OFFLINE_APPLY"
    assert not any(urlparse(url).path == "/index.bsseogu" for url in backend.urls)
    assert not any(
        "DOM_000001006001000000" in url for url in backend.urls
    )

    serialized = repr(rows)
    for secret in (
        "SECRET_SERVER_CLOCK",
        "SECRET_INSTRUCTOR",
        "SECRET_PHONE",
        "SECRET_FREE_FORM",
        "private@example.com",
        "SECRET_ATTACHMENT",
        "SECRET_CARD_PHONE",
        "SECRET_DETAIL_PHONE",
        "SECRET_CITY_FREE_FORM",
        "city-private@example.com",
        "SECRET_ADDRESS",
        "SECRET_APPLICANT",
        "SECRET_APPLICATION_OFFICE",
        "SECRET_HAPPY",
        "SECRET_BACKEND_BEAN",
    ):
        assert secret not in serialized
    assert local["raw_fields"]["login_applicant_boundary_never_fetched"] is True
    assert local["raw_fields"]["instructor_value_never_read"] is True
    assert city["raw_fields"]["inquiry_phone_value_never_read"] is True
    assert city["raw_fields"]["free_form_detail_never_read"] is True


def test_transient_200_error_page_is_retried_atomically() -> None:
    rows, _parser, meta = _collect(_Backend(transient_local_detail=True))
    assert len(rows) == 4
    assert meta["snapshot_complete"] is True
    assert meta["network_retry_count"] == 1
    assert meta["network_requests"] == 25


@pytest.mark.parametrize(
    ("flag", "needle"),
    (
        ("bad_local_sentinel", "empty sentinel"),
        ("bad_chi_sentinel", "chi women empty sentinel"),
        ("bad_city_sentinel", "sentinel"),
        ("local_drift", "district boundary"),
        ("chi_drift", "chi women boundary"),
        ("city_drift", "Busan city boundary"),
        ("wrong_city_owner", "left Seo-gu owner"),
        ("wrong_city_identity", "detail identity changed"),
        ("wrong_local_title", "list/detail title differs"),
        ("changed_pii_label", "PII boundary"),
        ("changed_happy_pii_label", "PII boundary"),
    ),
)
def test_any_source_contract_failure_discards_both_ledgers(
    flag: str, needle: str
) -> None:
    rows, _parser, meta = _collect(_Backend(**{flag: True}))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert needle in meta["configured_collection_error"]


def test_caps_and_dedupe_changes_fail_closed() -> None:
    rows, _parser, meta = _collect(_Backend(), max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(_Backend(), detail_limit=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(_Backend(), max_requests=9)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_requests cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(
        _Backend(), dedupe_rows=lambda values: values[:1]
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]


def test_only_identity_and_value_bound_archive_date_errors_are_corrected() -> None:
    assert seogu._strict_flexible_range(
        "2025-06-27 ~ 2025-06-26",
        "legacy",
        identity="1784",
        kind="education",
    ) == ("2025-06-26", "2025-06-27")
    assert seogu._strict_flexible_range(
        "2008-01-01 ~ 2008-06-31",
        "legacy",
        identity="62",
        kind="application",
    ) == ("2008-01-01", "")
    assert seogu._strict_flexible_range(
        "2024-06-24~",
        "women legacy",
        identity="88",
        kind="women_education",
    ) == ("2024-06-24", "")
    assert seogu._strict_flexible_range(
        "2024-02-19~2024-02-02",
        "women legacy",
        identity="87",
        kind="women_education",
    ) == ("2024-02-02", "2024-02-19")
    with pytest.raises(seogu.BusanSeoguContractError):
        seogu._strict_flexible_range(
            "2025-06-27 ~ 2025-06-26",
            "new row",
            identity="9999",
            kind="education",
        )
    with pytest.raises(seogu.BusanSeoguContractError):
        seogu._strict_flexible_range(
            "2024-06-24 ~",
            "changed women spacing",
            identity="88",
            kind="women_education",
        )
    with pytest.raises(seogu.BusanSeoguContractError):
        seogu._strict_flexible_range(
            "2008-01-01 ~ 2008-06-31",
            "changed row",
            identity="62",
            kind="education",
        )


def test_wrong_target_fails_before_network() -> None:
    backend = _Backend()
    rows, _parser, meta = seogu.collect_busan_seogu_education(
        _target(url=seogu.BUSAN_SEOGU_PRIVATE_HISTORY_URL),
        fetcher=backend.fetch,
        session_factory=backend.session,
    )
    assert rows == []
    assert backend.urls == []
    assert "exact canonical" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_MUNICIPAL_AUDIT") != "1",
    reason="set RUN_LIVE_MUNICIPAL_AUDIT=1 for the 264-request live audit",
)
def test_live_complete_snapshot_matches_latest_audit_floor() -> None:
    rows, _parser, meta = seogu.collect_busan_seogu_education(
        _target(), today="2026-07-22"
    )
    assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
    assert meta["district_source_rows"] == 1805
    assert meta["district_data_pages"] == 181
    assert meta["district_current_count"] == 27
    assert meta["women_source_rows"] == 114
    assert meta["women_current_count"] == 6
    assert meta["happy_source_rows"] == 1
    assert meta["happy_current_count"] == 1
    assert meta["library_source_rows"] == 1
    assert meta["library_current_count"] == 0
    assert meta["city_source_rows"] == 20
    assert meta["city_current_count"] == 20
    assert meta["source_rows"] == 1941
    assert meta["current_source_count"] == 54
    assert meta["data_pages"] == 195
    assert meta["list_requests"] == 210
    assert meta["required_list_requests"] == 210
    assert meta["sentinel_requests"] == 5
    assert meta["stability_rechecks"] == 10
    assert meta["network_requests"] >= 264
    assert meta["application_control_count"] == 8
    assert meta["offline_application_count"] == 1
    assert meta["source_status_counts"] == {"접수마감": 1932, "접수중": 9}
    assert meta["current_status_counts"] == {"접수중": 9, "접수마감": 45}
    assert len(rows) == meta["current_source_count"]
