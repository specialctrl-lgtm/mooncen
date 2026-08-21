from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml
from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as municipal
from Crawler import municipal_seoul_junggu


PROVIDER = municipal.SEOUL_JUNGGU_EDUCATION_PROVIDER
TARGET_URL = "https://www.junggu.seoul.kr/booking/content.do?cmsid=16554"
ROOT = Path(__file__).resolve().parents[1]


def _target(url: str = TARGET_URL) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=PROVIDER,
        name="서울특별시 중구 교육통합 교육·강좌",
        branch="서울특별시 중구 교육통합",
        url=url,
        source="test",
        region="서울특별시",
        extra={
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "municipality_code": "1114000000",
        },
    )


def _card(title: str, branch: str, href: str, status: str = "모집중") -> str:
    return f"""
    <li>
      <span class="small_tit">{branch}</span>
      <strong class="tit">{title}</strong>
      <div class="dl_wrap">
        <dl><dt>접수기간</dt><dd>2020년 1월 1일~2099년 12월 31일</dd></dl>
        <dl><dt>지원대상</dt><dd>중구민</dd></dl>
        <dl><dt>정원</dt><dd>20명</dd></dl>
        <dl><dt>문의처</dt><dd>02-3396-0000</dd></dl>
      </div>
      <a class="now_type" href="{href}">{status}</a>
    </li>
    """


def _list_page(total: int, cards: str) -> BeautifulSoup:
    return BeautifulSoup(
        f"""
        <html><body>
          <div class="page_num"><span>총 {total} 개</span></div>
          <div class="edu_list_wrap"><ul>{cards}</ul></div>
        </body></html>
        """,
        "lxml",
    )


def _native_detail(lecture_id: str) -> BeautifulSoup:
    return BeautifulSoup(
        f"""
        <html><body>
          <table>
            <tr><th>담당부서</th><td>디지털정책과</td></tr>
            <tr><th>강좌명</th><td>스마트폰 활용</td></tr>
            <tr><th>구분</th><td>정보화교육</td></tr>
            <tr><th>교육대상</th><td>중구민</td></tr>
            <tr><th>강사명</th><td>김강사</td></tr>
            <tr><th>교육기간</th><td>2099-08-01 ~ 2099-08-31</td></tr>
            <tr><th>교육시간</th><td>월 09:30 ~ 12:00</td></tr>
            <tr><th>접수기간</th><td>2020-01-01 ~ 2099-07-31</td></tr>
            <tr><th>접수방법</th><td>인터넷접수</td></tr>
            <tr><th>문의전화</th><td>02-1644-7128</td></tr>
            <tr><th>교육장소</th><td>구청 교육장</td></tr>
            <tr><th>정원</th><td>20명</td></tr>
            <tr><th>수강료</th><td>0원</td></tr>
            <tr><th>강좌소개</th><td>스마트폰 실습 과정</td></tr>
          </table>
          <a class="btn_write" href="/content.do?cmsid=14235&amp;command=write&amp;lec_idx={lecture_id}">신청</a>
        </body></html>
        """,
        "lxml",
    )


def _myhand_detail(
    business_id: int,
    title: str,
    *,
    branch: str,
    place: str,
    apply_form_id: int,
) -> BeautifulSoup:
    data = {
        "businessId": business_id,
        "organizationName": branch,
        "businessName": title,
        "businessType": "EDU",
        "categoryNames": "취미교육,중장년",
        "applyStartDate": "2020-01-01",
        "applyStartTime": "09:00:00",
        "applyEndDate": "2099-07-31",
        "applyEndTime": "23:59:59",
        "studyStartDate": "2099-08-01",
        "studyEndDate": "2099-08-31",
        "studyStartTime": "10:00:00",
        "studyEndTime": "12:00:00",
        "studyWeekend": "SAT",
        "place": place,
        "host": branch,
        "contact": "02-3396-1234",
        "clientCount": 15,
        "cost": 10000,
        "applyFormId": apply_form_id,
        "useBusinessSubYn": "N",
        "organizationCodeId": None,
        "clientRule": "서울 중구민",
        "content": "교육내용: 실습 중심 강좌",
    }
    return BeautifulSoup(
        f"<html><body><script>const app = {{ businessData: {json.dumps(data, ensure_ascii=False)}, other: true }};</script></body></html>",
        "lxml",
    )


def test_seoul_junggu_complete_fixture_uses_official_ids_and_actual_apply_urls(monkeypatch) -> None:
    page_one = _list_page(
        3,
        _card(
            "스마트폰 활용",
            "구청 교육장",
            "https://www.junggu.seoul.kr/content.do?cmsid=14235&command=view&lec_idx=3369",
        )
        + _card(
            "도예 교실",
            "중림동",
            "https://myhand.junggu.seoul.kr/user/business/detail/1001",
            "종료",
        ),
    )
    page_two = _list_page(
        3,
        _card(
            "자치회관 이용안내(물품공유, 대관)",
            "신당5동",
            "https://myhand.junggu.seoul.kr/user/business/detail/1002",
        ),
    )

    def fake_fetch(_session, url: str, timeout: int = 20) -> BeautifulSoup:
        del timeout
        parsed = urlparse(url)
        if parsed.path == municipal.SEOUL_JUNGGU_EDUCATION_LIST_PATH:
            page = int((parse_qs(parsed.query).get("page") or ["1"])[0])
            return {1: page_one, 2: page_two}[page]
        if parsed.netloc == municipal.SEOUL_JUNGGU_EDUCATION_LIST_HOST:
            return _native_detail("3369")
        business_id = int(parsed.path.rstrip("/").split("/")[-1])
        if business_id == 1001:
            return _myhand_detail(
                1001,
                "도예 교실",
                branch="중림동 주민센터",
                place="중림동 주민센터 강의실",
                apply_form_id=501,
            )
        return _myhand_detail(
            1002,
            "자치회관 이용안내(물품공유, 대관)",
            branch="신당5동",
            place="신당5동 주민센터",
            apply_form_id=502,
        )

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    rows, parser, meta = municipal.collect_seoul_junggu_education(
        _target(), timeout=5, max_pages=5, detail_limit=3
    )

    assert parser == "seoul_junggu_education_full_pagination+dual_detail"
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["declared_total"] == 3
    assert meta["raw_card_count"] == 3
    assert meta["unique_official_ids"] == 3
    assert meta["excluded_non_course_count"] == 1
    assert len(rows) == 2

    by_id = {row["provider_course_id"]: row for row in rows}
    native_id = f"{PROVIDER}:junggu-lecture:3369"
    myhand_id = f"{PROVIDER}:myhand-business:1001"
    assert set(by_id) == {native_id, myhand_id}

    native = by_id[native_id]
    assert native["prefer_incoming_provider_course_id"] is True
    assert native["branch"] == "디지털정책과"
    assert native["venue_name"] == "구청 교육장"
    assert native["application_url"] == (
        "https://www.junggu.seoul.kr/content.do?cmsid=14235&command=write&lec_idx=3369"
    )
    assert native["status"] == "OPEN"

    myhand = by_id[myhand_id]
    assert myhand["prefer_incoming_provider_course_id"] is True
    assert myhand["branch"] == "중림동 주민센터"
    assert myhand["venue_name"] == "중림동 주민센터 강의실"
    assert myhand["application_url"] == (
        "https://myhand.junggu.seoul.kr/user/business/detail/apply/1001/501"
    )
    assert myhand["status"] == "OPEN"

    for row in rows:
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["domain_category"] == "교육·강좌"
        assert row["collection_category"] == "공공예약"
        assert row["preserve_branch"] is True


def test_seoul_junggu_declared_count_and_duplicate_mismatch_blocks_details(monkeypatch) -> None:
    duplicate = _card(
        "스마트폰 활용",
        "구청 교육장",
        "https://www.junggu.seoul.kr/content.do?cmsid=14235&command=view&lec_idx=3369",
    )
    pages = {1: _list_page(3, duplicate + duplicate), 2: _list_page(3, "")}

    def fake_fetch(_session, url: str, timeout: int = 20) -> BeautifulSoup:
        del timeout
        parsed = urlparse(url)
        assert parsed.path == municipal.SEOUL_JUNGGU_EDUCATION_LIST_PATH
        page = int((parse_qs(parsed.query).get("page") or ["1"])[0])
        return pages[page]

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
    rows, _parser, meta = municipal.collect_seoul_junggu_education(
        _target(), timeout=5, max_pages=5, detail_limit=10
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["pagination_complete"] is False
    assert meta["detail_pages"] == 0
    assert meta["duplicate_cards"] == 1
    assert "declared total 3 does not match 2" in meta["configured_collection_error"]
    assert "duplicate official IDs" in meta["configured_collection_error"]


def test_seoul_junggu_detail_cap_blocks_partial_snapshot(monkeypatch) -> None:
    page = _list_page(
        2,
        _card(
            "스마트폰 활용",
            "구청 교육장",
            "https://www.junggu.seoul.kr/content.do?cmsid=14235&command=view&lec_idx=3369",
        )
        + _card(
            "도예 교실",
            "중림동",
            "https://myhand.junggu.seoul.kr/user/business/detail/1001",
        ),
    )
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", lambda *_args, **_kwargs: page)
    rows, _parser, meta = municipal.collect_seoul_junggu_education(
        _target(), timeout=5, max_pages=5, detail_limit=1
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert meta["detail_pages"] == 0
    assert "detail_limit cap allows 1 of 2" in meta["configured_collection_error"]


def test_seoul_junggu_myhand_external_owner_and_content_date_contract() -> None:
    target = _target()
    listed = {
        "official_id": "2001",
        "raw_url": "https://myhand.junggu.seoul.kr/user/business/detail/2001",
        "title": "작가와의 만남",
        "branch": "손기정문화도서관",
        "list_status": "모집중",
        "list_pairs": {},
    }
    data = {
        "businessId": 2001,
        "organizationName": "중구도서관",
        "businessName": "작가와의 만남",
        "businessType": "PROGRAM",
        "applyStartDate": "2020-01-01",
        "applyEndDate": "2099-07-31",
        "studyStartDate": None,
        "studyEndDate": None,
        "content": "교육일시: 2099. 8. 20.(목) 19:00~21:00",
        "host": "손기정문화도서관",
        "place": "손기정문화도서관 1층 라운지",
        "organizationCodeId": 2,
        "link": "https://www.junggulib.or.kr/program/lectureDetail.do?lectureIdx=60001",
        "applyFormId": None,
        "useBusinessSubYn": "N",
    }
    row, valid, current = municipal.seoul_junggu_myhand_row(target, listed, data)
    assert valid is True
    assert current is True
    assert row["end_date"].isoformat() == "2099-08-20"
    assert row["status"] == "OPEN"
    assert row["application_url"] == data["link"]

    data["applyStartDate"] = "2020-01-01"
    data["applyEndDate"] = "2020-01-02"
    closed, valid, current = municipal.seoul_junggu_myhand_row(target, listed, data)
    assert valid is True
    assert current is True
    assert closed["status"] == "CLOSED"
    assert "application_url" not in closed
    assert closed["raw_fields"]["clear_application_url"] is True


def test_seoul_junggu_target_and_dispatch_are_exact(monkeypatch) -> None:
    assert municipal.is_seoul_junggu_education_target(_target()) is True
    assert municipal.is_seoul_junggu_education_target(
        _target("https://www.junggu.seoul.kr/booking/content.do?cmsid=16558")
    ) is False
    assert municipal.is_seoul_junggu_education_target(
        _target("https://www.junggu.seoul.kr/booking/content.do?cmsid=16554&page=1")
    ) is False

    sentinel = ([{"title": "education"}], "seoul-junggu", {"pages": 1})
    monkeypatch.setattr(
        municipal_seoul_junggu,
        "collect_seoul_junggu_education",
        lambda *_args, **_kwargs: sentinel,
    )
    assert municipal.collect_from_url(
        _target(), timeout=5, max_depth=0, max_pages=50, detail_limit=500
    ) == sentinel


def test_seoul_junggu_config_is_operational_and_full_snapshot_locked() -> None:
    document = yaml.safe_load(
        (ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml").read_text(
            encoding="utf-8"
        )
    )
    target = next(row for row in document["targets"] if row.get("provider") == PROVIDER)
    assert target["url"] == TARGET_URL
    assert target["municipality_code"] == "1114000000"
    assert target["crawler_status"] == "ready"
    assert target["full_snapshot_required"] is True
    assert target["service_group"] == "공공강좌"
    assert target["service_group_policy"] == "locked"
    assert target["last_quality"]["parser"] == municipal_seoul_junggu.SEOUL_JUNGGU_EDUCATION_PARSER
    assert target["last_quality"]["collected"] == 135
    assert target["last_quality"]["snapshot_complete"] is True

    operational = yaml.safe_load(
        (ROOT / "config" / "municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )
    entry = next(row for row in operational["entries"] if row.get("provider") == PROVIDER)
    assert entry["validation_outcome"] == "collected"
    assert entry["row_count"] == 135
