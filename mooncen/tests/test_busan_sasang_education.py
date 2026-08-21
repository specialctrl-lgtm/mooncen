from __future__ import annotations

from html import escape
import os
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_busan_sasang as sasang


class _Response:
    def __init__(self, url: str, html: str, status_code: int = 200) -> None:
        self.url = url
        self.content = html.encode("utf-8")
        self.text = html
        self.status_code = status_code
        self.history: list[Any] = []


class _Session:
    def close(self) -> None:
        return None


def _target(
    *,
    provider: str = sasang.BUSAN_SASANG_PROVIDER,
    candidate_id: str = sasang.BUSAN_SASANG_CANDIDATE_ID,
    url: str = sasang.BUSAN_SASANG_CANONICAL_URL,
) -> dict[str, str]:
    return {
        "provider": provider,
        "candidate_id": candidate_id,
        "url": url,
        "name": "부산광역시 사상구 교육",
    }


_LOCAL_ROWS = (
    {
        "identity": "90001",
        "title": "사상 미래교실",
        "status": "접수중",
        "status_class": "ing",
        "start": "2099-08-01",
        "end": "2099-08-31",
    },
    {
        "identity": "90002",
        "title": "사상 대기교실",
        "status": "접수대기",
        "status_class": "wait",
        "start": "2099-09-01",
        "end": "2099-09-30",
    },
)


def _local_card(
    row: dict[str, str],
    *,
    drift: bool = False,
    missing_institution: bool = False,
    missing_schedule: bool = False,
) -> str:
    title = "변경된 사상 미래교실" if drift and row["identity"] == "90001" else row["title"]
    institution = "" if missing_institution and row["identity"] == "90002" else "평생학습관"
    schedule = "" if missing_schedule and row["identity"] == "90002" else "10:00 ~ 12:00"
    return f"""
      <li><dl><dt>
        <span class="divKind">인문/교양/독서</span>
        <span class="divPart">성인</span>
        <span class="stat {row['status_class']}">{row['status']}</span>
        <span class="tit"><a href="#" onclick="url_chk('{sasang.BUSAN_SASANG_MENU}',
          '{row['identity']}','1')">{escape(title)}</a></span>
      </dt><dd><ul>
        <li><span class="name">접수기간 :</span>2099-07-01 ~ 2099-07-31</li>
        <li><span class="name">교육기간 :</span>{row['start']} ~ {row['end']}</li>
        <li><span class="name">시　　간 :</span>{schedule}</li>
        <li><span class="name">장　　소 :</span>사상평생학습관</li>
        <li><span class="name">모집인원 :</span>정원20명, SECRET_LIST_ENROLLMENT 3명</li>
        <li><span class="name">운영기관 :</span>{institution}</li>
        <li><span class="name">접수방법 :</span>인터넷</li>
      </ul></dd></dl></li>
    """


def _local_page(
    page: int,
    *,
    bad_sentinel: bool = False,
    drift: bool = False,
    missing_institution: bool = False,
    missing_schedule: bool = False,
) -> str:
    cards = ""
    if page == 1 or bad_sentinel:
        cards = "".join(
            _local_card(
                row,
                drift=drift,
                missing_institution=missing_institution,
                missing_schedule=missing_schedule,
            )
            for row in _LOCAL_ROWS
        )
    root = f'<div class="bbs_edu"><ul>{cards}</ul></div>' if cards else ""
    return f"""
      <html><head><meta charset="utf-8"><title>
        ( 전체 ) 의 목록 | 교육/강좌/공연 | 사상구 통합예약 시스템
      </title></head><body>
        <form id="applyVO" name="applyVO" method="post" action="{sasang.BUSAN_SASANG_LIST_PATH}">
          <input name="pageIndex" value="{page}">
          <input name="menuCd" value="{sasang.BUSAN_SASANG_MENU}">
          <input name="searchStartDate" value="2099-07-22">
          <input name="searchEndate" value="">
          <select name="searchDateType"><option value="eduDate" selected>교육기간</option></select>
        </form>
        <p class="boardPage">총게시물 : <strong>2</strong> 건 / 페이지 :
          <strong>{page}</strong> /1</p>
        {root}
      </body></html>
    """


def _local_detail(
    row: dict[str, str],
    *,
    wrong_identity: bool = False,
    missing_institution: bool = False,
    missing_schedule: bool = False,
    detail_status: str | None = None,
) -> str:
    identity = "99999" if wrong_identity else row["identity"]
    active = row["status"] == "접수중"
    rendered_status = detail_status or row["status"]
    control = (
        f'<span class="btnBbs"><a href="{sasang.BUSAN_SASANG_APPLY_PATH}?'
        f'menuCd={sasang.BUSAN_SASANG_MENU}&amp;couIdx={row["identity"]}">신청하기</a></span>'
        if active else ""
    )
    values = (
        ("교육구분", "인문교양"),
        *((("교육시간", "10:00 ~ 12:00"),) if not (
            missing_schedule and row["identity"] == "90002"
        ) else ()),
        ("교육대상", "성인"),
        ("수강료", "무료"),
        ("인터넷모집", "인원 20명"),
        ("현재신청자수", "SECRET_DETAIL_ENROLLMENT"),
        ("대기자모집", "SECRET_WAITLIST"),
        *((("교육기관", "평생학습관"),) if not (
            missing_institution and row["identity"] == "90002"
        ) else ()),
        ("접수방법", "인터넷"),
        ("교육장소", "사상평생학습관"),
        ("문의전화", "SECRET_PHONE 051-000-0000"),
        ("접수기간", "2099-07-01 09:00 ~ 2099-07-31 18:00"),
        ("강좌기간", f"{row['start']} ~ {row['end']}"),
        ("강사명", "SECRET_INSTRUCTOR 010-1111-2222"),
        ("첨부파일", "SECRET_ATTACHMENT"),
    )
    fields = "".join(
        f'<li><span class="name">{label}</span>{escape(value)}</li>'
        for label, value in values
    )
    return f"""
      <html><head><meta charset="utf-8"><title>교육 상세</title></head><body>
        <form name="sfrm" method="post" action="{sasang.BUSAN_SASANG_DETAIL_PATH}">
          <input name="pageIndex" value="1"><input name="couIdx" value="{identity}">
          <input name="menuCd" value="{sasang.BUSAN_SASANG_MENU}">
        </form>
        <div class="edu_vtype"><dl class="bbs_infor"><dt>
          <span class="stat {row['status_class']}">{rendered_status}</span>{escape(row['title'])}
        </dt><dd><ul class="infor">{fields}</ul></dd></dl>
        <div class="infor_con">SECRET_FREE_FORM private@example.test</div></div>
        {control}
      </body></html>
    """


def _platform_row(
    sequence: int,
    *,
    identity: str,
    title: str,
    external_url: str = "",
) -> str:
    if external_url:
        title_action = f'href="{escape(external_url, quote=True)}" target="_blank"'
        action = f'<a href="{escape(external_url, quote=True)}">수강신청</a>'
    else:
        onclick = f"fn_learning_detail('{identity}'); return false;"
        title_action = f'href="javascript:;" onclick="{onclick}"'
        action = f'<a href="javascript:;" onclick="{onclick}">수강신청</a>'
    return f"""
      <tr><td>{sequence}</td><td class="subject"><a {title_action}>
        <span class="tit">{escape(title)}</span><span class="org">사상구청</span></a></td>
        <td><span>무료</span><span>SECRET_LIST_INSTRUCTOR</span></td>
        <td><span class="s_type blue"><em class="hidden">교육기간</em>
          2099.08.01~2099.08.31<pre>수, 10:00~12:00</pre></span></td>
        <td><span class="s_type indigo1"><em class="hidden">모집인원</em>20명</span>
          <span class="s_type red1"><em class="hidden">일반접수</em>
          2099.07.01~2099.07.31 (접수인원 : SECRET)</span></td>
        <td><span class="s_type2"><em class="hidden">선착순</em></span>
          <span class="s_btn">접수중</span></td><td>{action}</td></tr>
    """


def _platform_page(page: int, *, drift: bool = False, bad_external: bool = False) -> str:
    if page == 1:
        external = (
            "https://evil.example/user/apply/view.sasang?menuCd="
            f"{sasang.BUSAN_SASANG_PLATFORM_MENU}&couIdx=90001&pageIndex=1"
            if bad_external
            else "https://www.sasang.go.kr/user/apply/view.sasang?menuCd="
            f"{sasang.BUSAN_SASANG_PLATFORM_MENU}&couIdx=90001&pageIndex=1"
        )
        body = _platform_row(
            2,
            identity="LEARNING_00090001",
            title="변경된 플랫폼 고유과정" if drift else "플랫폼 고유과정",
        ) + _platform_row(
            1, identity=external, title=_LOCAL_ROWS[0]["title"], external_url=external
        )
    else:
        body = '<tr><td colspan="7">등록된 교육강좌가 없습니다.</td></tr>'
    return f"""
      <html><head><meta charset="utf-8"><title>부산평생학습플랫폼</title></head><body>
        <form id="learningVO" method="post" action="{sasang.BUSAN_LIFELONG_LIST_PATH}">
          <input name="inst_id" value="{sasang.BUSAN_LIFELONG_SASANG_OFFICE}">
          <input name="display_type" value="2"><input name="pageIndex" value="{page}">
          <input name="l_search_ch" value="0"><select id="o_search_ch"><option
          value="{sasang.BUSAN_LIFELONG_SASANG_OFFICE}" selected>사상구청</option></select>
          <select id="learning_state"><option value="0" selected>전체</option></select>
        </form><table><thead><tr><th>번호</th><th>강좌명 / 교육기관</th>
          <th>재료비 / 강사</th><th>교육기간 / 교육시간</th>
          <th>신청기간 / 접수인원 / 대기자</th><th>상태</th><th>보기</th>
        </tr></thead><tbody>{body}</tbody></table>
        <a class="page_nextend" href="?pageIndex=1"
          onclick="fn_list(1,'');return false;">마지막</a>
      </body></html>
    """


def _platform_detail(*, wrong_title: bool = False) -> str:
    safe = {
        "강좌분류": "인문교양", "교육대상": "부산시민",
        "교육장소": "사상평생학습관", "총 교육시간": "8시간",
        "교육기간": "2099.08.01 ~ 2099.08.31", "교육시간": "수 10:00 ~ 12:00",
        "수강료": "무료", "재료비": "무료", "우선모집기간": "해당없음",
        "일반모집기간": "2099.07.01 ~ 2099.07.31", "모집방법": "선착순",
        "신청상태": "일반 접수중", "교육상태": "교육예정", "결제방법": "무료",
    }
    unsafe = {
        "회차명": "SECRET_SESSION", "문의전화": "SECRET_PHONE 051-000-0000",
        "접수인원": "SECRET_ENROLLMENT", "강좌소개": "SECRET_DESCRIPTION private@example.test",
        "강좌소개 첨부파일": "SECRET_ATTACHMENT", "강사": "SECRET_INSTRUCTOR 010-1111-2222",
        "강의계획서": "SECRET_PLAN", "주의사항": "SECRET_WARNING",
        "검색키워드": "SECRET_KEYWORD", "강좌제한": "SECRET_LIMIT",
    }
    definitions = "".join(
        f'<dl><dt>{label}</dt><dd>{escape(safe.get(label, unsafe.get(label, "")))}</dd></dl>'
        for label in sasang._PLATFORM_DETAIL_REQUIRED
    )
    title = "다른 플랫폼 고유과정" if wrong_title else "플랫폼 고유과정"
    return f"""
      <html><head><meta charset="utf-8"><title>부산평생학습플랫폼</title></head><body>
        <form><input name="inst_id" value="{sasang.BUSAN_LIFELONG_SASANG_OFFICE}">
          <input name="lng_id" value="LEARNING_00090001"></form>
        <h2 class="enrolTit"><span>[사상구청]</span>{title}</h2>
        <div class="form_group">{definitions}</div>
        <a id="learning_aply_btn" onclick="fn_learning_apply(); return false;">일반모집신청</a>
      </body></html>
    """


def _city_card(*, wrong_owner: bool = False) -> str:
    branch = "부산진구 부암1동 주민자치회" if wrong_owner else "사상구 괘법동 주민자치회"
    values = (
        ("기관", branch), ("대상", "제한없음"), ("장소", "프로그램실"),
        ("일자", "[신청] 2099-07-01 ~ 2099-07-31 [행사] 2099-08-01 ~ 2099-08-31"),
        ("방법", "온라인(선착순)"), ("문의", "SECRET_CARD_PHONE 051-000-0000"),
    )
    definitions = "".join(
        f"<dt>{label}</dt><dd>{escape(value)}</dd>" for label, value in values
    )
    return f"""
      <li><a class="reserveItem" onclick="fn_viewProgrm('312', '9001');return false;">
        <div class="infoBox"><p class="tit" title="주민센터 요가">주민센터 요가</p>
        <span class="statusMark">접수중</span><dl>{definitions}</dl></div></a></li>
    """


def _city_page(page: int, *, bad_sentinel: bool = False, wrong_owner: bool = False) -> str:
    cards = _city_card(wrong_owner=wrong_owner) if page == 1 or bad_sentinel else ""
    root = f'<ul class="reserveList">{cards}</ul>' if cards else ""
    return f"""
      <html><head><meta charset="utf-8"><title>{sasang._CITY_LIST_TITLE}</title></head><body>
        <form id="srchForm" name="srchForm" method="get" action="/lctre">
          <input name="curPage" value="{page}"><select name="srchGugun"><option
          value="9" selected>사상구</option></select><select name="srchResveInsttCd"><option
          value="33" selected>주민자치회</option></select>
        </form>{root}<div class="paginate"><a class="pgEnd"
          href="?curPage=1&amp;srchGugun=9&amp;srchResveInsttCd=33">마지막</a></div>
      </body></html>
    """


def _city_detail(*, wrong_identity: bool = False) -> str:
    program = "9999" if wrong_identity else "9001"
    values = (
        ("운영기간", "2099-08-01 ~ 2099-08-31"),
        ("신청기간", "2099-07-01 ~ 2099-07-31"),
        ("취소여부", "취소 가능"), ("신청방법", "온라인(선착순)"),
        ("수강료", "무료"), ("요일 /시간", "수 / 10:00 ~ 12:00"),
        ("문의전화", "SECRET_CITY_PHONE 051-000-0000"),
        ("운영기관", "사상구 괘법동 주민자치회"), ("대상", "제한없음"),
        ("첨부파일", "SECRET_CITY_ATTACHMENT"),
    )
    definitions = "".join(
        f"<dl><dt>{label}</dt><dd>{escape(value)}</dd></dl>" for label, value in values
    )
    return f"""
      <html><head><meta charset="utf-8"><title>{sasang._CITY_LIST_TITLE}</title></head><body>
        <form id="viewForm" method="post"><input name="resveGroupSn" value="312">
          <input name="progrmSn" value="{program}"><div class="contHeader"><h3
          class="titPage">주민센터 요가<span class="statusMark">접수중</span></h3></div>
          <div class="reserveStateWrap"><div class="reserveStateInfo">{definitions}</div>
          <div class="reserveBtnWrap"><a class="btnTypeXL">예약하기</a></div></div>
          <div class="reserveDetail">SECRET_CITY_FREE_FORM city-private@example.test</div>
        </form>
      </body></html>
    """


class _Backend:
    def __init__(self, **flags: Any) -> None:
        self.flags = flags
        self.urls: list[str] = []
        self.calls: dict[str, int] = {}
        self._lock = Lock()

    def session(self) -> _Session:
        return _Session()

    def fetch(self, _session: Any, url: str, _timeout: int) -> _Response:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        with self._lock:
            self.urls.append(url)
            self.calls[url] = self.calls.get(url, 0) + 1
            calls = self.calls[url]
        if parsed.hostname == sasang.BUSAN_SASANG_HOST:
            if parsed.path == sasang.BUSAN_SASANG_LIST_PATH:
                page = int(query["pageIndex"][0])
                return _Response(
                    url,
                    _local_page(
                        page,
                        bad_sentinel=bool(self.flags.get("local_bad_sentinel") and page == 2),
                        drift=bool(self.flags.get("local_drift") and page == 1 and calls > 1),
                        missing_institution=bool(self.flags.get("local_missing_institution")),
                        missing_schedule=bool(self.flags.get("local_missing_schedule")),
                    ),
                )
            if parsed.path == sasang.BUSAN_SASANG_DETAIL_PATH:
                identity = query["couIdx"][0]
                row = next(value for value in _LOCAL_ROWS if value["identity"] == identity)
                return _Response(
                    url,
                    _local_detail(
                        row,
                        wrong_identity=bool(self.flags.get("local_wrong_detail")),
                        missing_institution=bool(self.flags.get("local_missing_institution")),
                        missing_schedule=bool(self.flags.get("local_missing_schedule")),
                    ),
                )
        if (
            parsed.hostname == sasang._lifelong.BUSAN_LIFELONG_HOST
            and parsed.path == sasang.BUSAN_LIFELONG_LIST_PATH
        ):
            page = int(query["pageIndex"][0])
            return _Response(
                url,
                _platform_page(
                    page,
                    drift=bool(self.flags.get("platform_drift") and page == 1 and calls > 1),
                    bad_external=bool(self.flags.get("bad_external")),
                ),
            )
        if (
            parsed.hostname == sasang._lifelong.BUSAN_LIFELONG_HOST
            and parsed.path == sasang.BUSAN_LIFELONG_DETAIL_PATH
        ):
            return _Response(
                url,
                _platform_detail(wrong_title=bool(self.flags.get("platform_wrong_detail"))),
            )
        if parsed.hostname == sasang.BUSAN_CITY_HOST and parsed.path == sasang.BUSAN_CITY_LIST_PATH:
            page = int(query["curPage"][0])
            return _Response(
                url,
                _city_page(
                    page,
                    bad_sentinel=bool(self.flags.get("city_bad_sentinel") and page == 2),
                    wrong_owner=bool(self.flags.get("wrong_city_owner")),
                ),
            )
        if parsed.hostname == sasang.BUSAN_CITY_HOST and parsed.path == sasang.BUSAN_CITY_DETAIL_PATH:
            return _Response(
                url,
                _city_detail(wrong_identity=bool(self.flags.get("city_wrong_detail"))),
            )
        raise AssertionError(f"unexpected URL {url}")


def _collect(backend: _Backend, **kwargs: Any):
    return sasang.collect_busan_sasang_education(
        _target(),
        today="2099-07-22",
        max_pages=kwargs.pop("max_pages", 2),
        detail_limit=kwargs.pop("detail_limit", 8),
        max_requests=kwargs.pop("max_requests", 30),
        max_workers=3,
        fetcher=backend.fetch,
        session_factory=backend.session,
        sleeper=lambda _seconds: None,
        **kwargs,
    )


def test_exact_target_and_owner_boundaries() -> None:
    assert sasang.is_busan_sasang_education_target(_target()) is True
    assert sasang.BUSAN_SASANG_OWNER_BOUNDARY_AUDIT[sasang.BUSAN_LIFELONG_PROVIDER][
        "office_code"
    ] == "OFFICE_00002633"
    assert not sasang.is_busan_sasang_education_target(_target(provider="WRONG"))
    assert not sasang.is_busan_sasang_education_target(
        _target(candidate_id="MUNI_IR_WRONG")
    )
    assert not sasang.is_busan_sasang_education_target(
        _target(url=sasang.BUSAN_SASANG_CANONICAL_URL + "&other=1")
    )


def test_complete_atomic_three_ledger_snapshot_and_privacy() -> None:
    backend = _Backend()
    rows, parser, meta = _collect(backend)

    assert parser == sasang.BUSAN_SASANG_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["district_source_rows"] == 2
    assert meta["platform_source_rows"] == 2
    assert meta["platform_external_duplicate_rows"] == 1
    assert meta["platform_external_matching_current_district"] == 1
    assert meta["platform_native_rows"] == 1
    assert meta["platform_native_current_count"] == 1
    assert meta["city_source_rows"] == 1
    assert meta["source_total"] == 5
    assert meta["duplicate_source_rows"] == 1
    assert meta["unique_education_source_rows"] == 4
    assert meta["current_source_count"] == 4
    assert meta["status_counts"] == {"OPEN": 3, "SCHEDULED": 1}
    assert meta["application_control_count"] == 3
    assert meta["required_list_requests"] == 10
    assert meta["required_detail_requests"] == 4
    assert meta["network_requests"] == 14
    assert meta["sentinel_requests"] == 4
    assert meta["stability_rechecks"] == 4
    assert len(rows) == 4
    assert all(row["municipality_code"] == "2653000000" for row in rows)
    assert all(row["raw_fields"]["detail_verified"] is True for row in rows)

    rendered = repr(rows)
    for secret in (
        "SECRET_LIST_ENROLLMENT", "SECRET_DETAIL_ENROLLMENT", "SECRET_WAITLIST",
        "SECRET_PHONE", "SECRET_INSTRUCTOR", "SECRET_ATTACHMENT", "SECRET_FREE_FORM",
        "SECRET_LIST_INSTRUCTOR", "SECRET_ENROLLMENT", "SECRET_DESCRIPTION",
        "SECRET_CITY_PHONE", "SECRET_CITY_ATTACHMENT", "SECRET_CITY_FREE_FORM",
        "private@example.test", "city-private@example.test", "010-", "051-",
    ):
        assert secret not in rendered
    assert not any(urlparse(url).path == sasang.BUSAN_SASANG_APPLY_PATH for url in backend.urls)


def test_officially_omitted_institution_uses_district_fallback() -> None:
    rows, _parser, meta = _collect(_Backend(local_missing_institution=True))

    assert meta["snapshot_complete"] is True
    row = next(value for value in rows if value["provider_course_id"].endswith(":90002"))
    assert row["branch"] == sasang.BUSAN_SASANG_MUNICIPALITY_NAME
    assert row["provider_organizer"] == "사상구청"
    assert row["raw_fields"]["list_operator_omitted"] is True
    assert row["raw_fields"]["detail_institution_omitted"] is True
    assert row["raw_fields"]["institution_fallback_used"] is True


def test_officially_omitted_schedule_uses_explicit_fallback() -> None:
    rows, _parser, meta = _collect(_Backend(local_missing_schedule=True))

    assert meta["snapshot_complete"] is True
    row = next(value for value in rows if value["provider_course_id"].endswith(":90002"))
    assert row["schedule_raw"] == "시간 별도 안내"
    assert row["raw_fields"]["list_schedule_omitted"] is True
    assert row["raw_fields"]["detail_schedule_omitted"] is True
    assert row["raw_fields"]["schedule_fallback_used"] is True


def test_official_reception_ended_to_education_active_status_pair_is_closed() -> None:
    source = {
        **_LOCAL_ROWS[1],
        "status": "접수종료",
        "status_class": "end",
    }
    parent = sasang._base_row(source["identity"], source["title"])
    parent.update(
        {
            "start_date": source["start"],
            "end_date": source["end"],
            "apply_start": "2099-07-01",
            "apply_end": "2099-07-31",
            "raw_fields": {
                "source_identity": source["identity"],
                "source_page": 1,
                "source_status": source["status"],
            },
        }
    )
    url = sasang.busan_sasang_detail_url(source["identity"], 1)
    soup = sasang.BeautifulSoup(
        _local_detail(
            source,
            missing_institution=True,
            detail_status="교육중",
        ),
        "lxml",
    )

    row = sasang._parse_local_detail(soup, url, parent)

    assert row["status"] == "CLOSED"
    assert row["reservation_available"] is False
    assert row["raw_fields"]["detail_source_status"] == "교육중"


@pytest.mark.parametrize(
    ("flag", "needle"),
    (
        ("local_bad_sentinel", "sentinel"),
        ("local_drift", "boundary page changed"),
        ("platform_drift", "complete censuses changed"),
        ("bad_external", "unsafe external detail"),
        ("wrong_city_owner", "left sasang owner"),
        ("local_wrong_detail", "hidden identity"),
        ("platform_wrong_detail", "title mismatch"),
        ("city_wrong_detail", "hidden identity"),
        ("city_bad_sentinel", "sentinel"),
    ),
)
def test_any_contract_failure_discards_the_whole_snapshot(flag: str, needle: str) -> None:
    rows, _parser, meta = _collect(_Backend(**{flag: True}))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert needle in meta["configured_collection_error"].casefold()


def test_caps_dedupe_and_wrong_target_fail_closed() -> None:
    rows, _parser, meta = _collect(_Backend(), detail_limit=3)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(
        _Backend(), dedupe_rows=lambda values: values[:-1]
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]

    backend = _Backend()
    rows, _parser, meta = sasang.collect_busan_sasang_education(
        _target(url=sasang.BUSAN_SASANG_CANONICAL_URL + "&other=1"),
        fetcher=backend.fetch,
        session_factory=backend.session,
    )
    assert rows == []
    assert backend.urls == []
    assert "exact canonical" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("MOONCEN_RUN_BUSAN_SASANG_LIVE") != "1",
    reason="set MOONCEN_RUN_BUSAN_SASANG_LIVE=1 for the exact 140-request audit",
)
def test_live_exact_snapshot_matches_2026_07_22_audit() -> None:
    rows, parser, meta = sasang.collect_busan_sasang_education(
        _target(), today="2026-07-22"
    )
    assert parser == sasang.BUSAN_SASANG_PARSER
    assert meta["snapshot_complete"] is True
    assert len(rows) == 122
    assert meta["district_source_rows"] == 35
    assert meta["district_data_pages"] == 5
    assert meta["district_source_status_counts"] == {
        "접수대기": 6, "접수중": 24, "접수마감": 5,
    }
    assert meta["platform_source_rows"] == 159
    assert meta["platform_external_duplicate_rows"] == 50
    assert meta["platform_external_matching_current_district"] == 1
    assert meta["platform_external_expired_owner_links"] == 49
    assert meta["platform_native_rows"] == 109
    assert meta["platform_native_current_count"] == 60
    assert meta["city_source_rows"] == 27
    assert meta["city_data_pages"] == 3
    assert meta["city_source_status_counts"] == {"접수중": 21, "접수마감": 6}
    assert meta["source_total"] == 221
    assert meta["duplicate_source_rows"] == 50
    assert meta["unique_education_source_rows"] == 171
    assert meta["current_source_count"] == 122
    assert meta["status_counts"] == {"SCHEDULED": 6, "OPEN": 45, "CLOSED": 71}
    assert meta["application_control_count"] == 24
    assert meta["required_list_requests"] == 18
    assert meta["required_detail_requests"] == 122
    assert meta["network_requests"] == 140
    assert meta["sentinel_requests"] == 4
    assert meta["stability_rechecks"] == 6
    assert meta["network_retry_count"] == 0
