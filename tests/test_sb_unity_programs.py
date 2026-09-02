from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as municipal


SB_PROVIDER = "MUNI_WWW_SB_GO_KR_CCBBD62B"
SB_EXISTING_PROVIDER = "MUNI_WWW_SB_GO_KR_FF615DE7"
SB_TARGET_URL = (
    "https://www.sb.go.kr/yeyak/selectUnityProgrmWebList.do?"
    "key=6950&insttTy=URINTY01&searchInsttNo=54&pageIndex=9&searchDong=x"
)

FACILITIES = (
    ("성북구평생학습관 대강의실", "서울특별시 성북구 종암로 167"),
    ("국민대학교 무용실 및 유도장", "서울특별시 성북구 정릉로 77"),
    ("장위청소년문화누림센터", "서울특별시 성북구 장월로 89-6"),
    ("월곡청소년센터", "서울특별시 성북구 화랑로13길 144"),
    ("성북청소년문화의집", "서울특별시 성북구 솔샘로 107"),
)
STATUSES = ("접수중", "대기자접수", "접수마감", "운영중")


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _target(
    provider: str = SB_PROVIDER,
    url: str = SB_TARGET_URL,
) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=provider,
        name="성북구 평생학습관",
        branch="서울특별시 성북구",
        url=url,
        source="test",
        priority=1,
        region="서울특별시 성북구",
        extra={"domain_category": "교육·강좌"},
    )


def _list_row(program_no: int, title: str, status: str, period: str = "2099.02.01 ~ 2099.11.30") -> str:
    return f"""
    <tr>
      <td>{program_no}</td>
      <td><a href="javascript:fnView('program', {program_no})">{title}</a></td>
      <td>2099.01.01 ~ 2099.01.31</td>
      <td>{period}</td>
      <td>성북구 평생교육과</td>
      <td>월 10:00~12:00</td>
      <td>1 / 15</td>
      <td>{status}</td>
    </tr>
    """


def _list_page() -> BeautifulSoup:
    rows: list[str] = []
    for offset in range(20):
        program_no = 4401 + offset
        rows.append(_list_row(program_no, f"성북 평생학습 {offset + 1}", STATUSES[offset % 4]))
    rows.append(_list_row(4401, "중복 링크", "접수중"))
    rows.append(_list_row(4499, "지난 강좌", "접수마감", "2020.01.01 ~ 2020.01.31"))
    rows.append(_list_row(4500, "임의 품질 테스트 강좌", "접수중"))
    rows.append(_list_row(4501, "운영 종료 강좌", "운영종료"))
    rows.append(_list_row(4502, "폐강 강좌", "폐강"))
    rows.append(
        """
        <tr><td>broken</td><td><a href="javascript:void(0)">깨진 행</a></td>
        <td></td><td></td><td></td><td></td><td></td><td>접수중</td></tr>
        """
    )
    return _soup(f'<table class="p-table"><tbody>{"".join(rows)}</tbody></table>')


def _detail_page(program_no: int, status: str = "") -> BeautifulSoup:
    facility_index = (program_no - 4401) // 4
    venue, address = FACILITIES[facility_index]
    return _soup(
        f"""
        <html><body>
          <table><tbody>
            <tr><th>ㆍ운영기관</th><td>성북구청</td></tr>
            <tr><th>ㆍ구분</th><td>기타</td></tr>
            <tr><th>ㆍ대상</th><td>제한없음</td></tr>
            <tr><th>ㆍ장소</th><td>{venue}</td></tr>
            <tr><th>ㆍ접수기간</th><td>2099.01.01 ~ 2099.01.31</td></tr>
            <tr><th>ㆍ교육기간</th><td>2099.02.01 ~ 2099.11.30</td></tr>
            <tr><th>ㆍ교육시간</th><td>10:00 ~ 12:00</td></tr>
            <tr><th>ㆍ교육요일</th><td>월</td></tr>
            <tr><th>ㆍ모집인원</th><td>15명</td></tr>
            <tr><th>ㆍ대기 모집인원</th><td>10명</td></tr>
            <tr><th>ㆍ이용요금</th><td>3,000원</td></tr>
            <tr><th>ㆍ재료비/교재비</th><td>20,000원</td></tr>
            <tr><th>ㆍ접수방법</th><td>온라인, 방문</td></tr>
            <tr><th>ㆍ문의전화</th><td>02-2241-2420</td></tr>
            {f'<tr><th>ㆍ접수상태</th><td>{status}</td></tr>' if status else ''}
          </tbody></table>
          <div class="map_info">{venue}</div>
          <div class="map_info_item address">{address}</div>
          <div class="map_info_item tel">02-2241-2420</div>
          <section>운영내용 성북구 공식 교육 프로그램 장소안내</section>
        </body></html>
        """
    )


def _fake_fetch_factory() -> tuple[object, list[str]]:
    fetched: list[str] = []

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("selectUnityProgrmWebList.do"):
            assert query == {
                "key": ["6950"],
                "insttTy": ["URINTY01"],
                "searchInsttNo": ["54"],
                "viewType": ["list"],
                "pageUnit": ["100"],
                "pageIndex": ["1"],
            }
            return _list_page()
        assert parsed.path.endswith("unityProgrmWebView.do")
        assert set(query) == {"key", "progrmNo"}
        assert query["key"] == ["6950"]
        program_no = int(query["progrmNo"][0])
        assert program_no != 4499, "expired rows must be removed before detail requests"
        return _detail_page(program_no)

    return fake_fetch, fetched


def test_sb_candidate_collects_twenty_current_programs_into_five_facility_branches(monkeypatch) -> None:
    fake_fetch, fetched = _fake_fetch_factory()
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    rows, parser, meta = municipal.collect_from_url(
        _target(), timeout=7, max_depth=0, max_pages=1, detail_limit=20
    )

    assert parser == "sb_unity_program_categories"
    assert len(rows) == 20
    assert len({row["provider_course_id"] for row in rows}) == 20
    assert {row["branch"] for row in rows} == {
        "성북구평생학습관",
        "국민대학교",
        "장위청소년문화누림센터",
        "월곡청소년센터",
        "성북청소년문화의집",
    }
    first = next(row for row in rows if row["provider_course_id"].endswith(":program:4401"))
    second_facility = next(row for row in rows if row["provider_course_id"].endswith(":program:4405"))
    assert first["branch"] == "성북구평생학습관"
    assert first["room"] == "대강의실"
    assert first["address"] == FACILITIES[0][1]
    assert first["target"] == "제한없음"
    assert first["category"] == "기타"
    assert first["fee"] == 3000
    assert first["material_fee"] == 20000
    assert first["capacity"] == 15
    assert first["waitlist_total"] == 10
    assert first["application_method_raw"] == "온라인, 방문"
    assert second_facility["branch"] == "국민대학교"
    assert second_facility["room"] == "무용실 및 유도장"

    for row in rows:
        program_no = row["raw_fields"]["program_no"]
        assert row["provider_course_id"] == f"{SB_PROVIDER}:program:{program_no}"
        assert parse_qs(urlparse(row["raw_url"]).query) == {
            "key": ["6950"],
            "progrmNo": [program_no],
        }
        if row["status"] in {"접수중", "대기자접수"}:
            assert row["reservation_available"] is True
            assert row["application_url"] == row["raw_url"]
            assert row["application_type"] == "ONLINE_RESERVATION"
        else:
            assert row["status"] in {"접수마감", "운영중"}
            assert row["reservation_available"] is False
            assert not row["application_url"]
            assert not row["application_type"]

    assert meta["pages"] == 1
    assert meta["detail_pages"] == 20
    assert meta["detail_candidates"] == 20
    assert meta["detail_enrichment_capped"] is False
    assert meta["discovered_links"] == 24
    assert meta["reservation_discovery_links"] == 10
    assert meta["expired_rows"] == 1
    assert meta["test_rows"] == 1
    assert meta["ended_rows"] == 2
    assert meta["invalid_count"] == 1
    assert meta["pagination_complete"] is True
    assert "configured_collection_error" not in meta
    assert len(fetched) == 21


def test_sb_explicit_no_data_is_clean_but_all_malformed_is_error(monkeypatch) -> None:
    pages = iter(
        [
            _soup(
                '<table class="p-table"><tbody><tr><td colspan="8">검색 결과가 없습니다.</td></tr></tbody></table>'
            ),
            _soup(
                '<table class="p-table"><tbody><tr><td>broken</td><td><a href="javascript:void(0)">깨진 행</a></td>'
                '<td></td><td></td><td></td><td></td><td></td><td>접수중</td></tr></tbody></table>'
            ),
        ]
    )

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", lambda *_args, **_kwargs: next(pages))

    no_data_rows, _parser, no_data_meta = municipal.collect_sb_unity_program_categories(
        _target(), timeout=7, max_pages=1, detail_limit=0
    )
    malformed_rows, _parser, malformed_meta = municipal.collect_sb_unity_program_categories(
        _target(), timeout=7, max_pages=1, detail_limit=0
    )

    assert no_data_rows == []
    assert no_data_meta["no_data_placeholders"] == 1
    assert no_data_meta["valid_count"] == 0
    assert no_data_meta["invalid_count"] == 0
    assert no_data_meta["pagination_complete"] is True
    assert no_data_meta["no_current_data"] is True
    assert no_data_meta["no_current_reason"] == "explicit no-data placeholder"
    assert "configured_collection_error" not in no_data_meta

    assert malformed_rows == []
    assert malformed_meta["no_data_placeholders"] == 0
    assert malformed_meta["valid_count"] == 0
    assert malformed_meta["invalid_count"] == 1
    assert malformed_meta["pagination_complete"] is False
    assert malformed_meta["no_current_data"] is False
    assert "all listed program rows were malformed" in malformed_meta["configured_collection_error"]


def test_sb_detail_status_renormalizes_application_and_filters_ended(monkeypatch) -> None:
    detail_statuses = {
        4401: "접수마감",
        4402: "접수중",
        4403: "운영종료",
        4404: "운영중",
    }

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("selectUnityProgrmWebList.do"):
            rows = [
                _list_row(4401, "상세 마감", "접수중"),
                _list_row(4402, "상세 접수", "접수마감"),
                _list_row(4403, "상세 종료", "접수중"),
                _list_row(4404, "상세 운영", "운영중"),
            ]
            return _soup(f'<table class="p-table"><tbody>{"".join(rows)}</tbody></table>')
        program_no = int(query["progrmNo"][0])
        return _detail_page(program_no, detail_statuses[program_no])

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    rows, _parser, meta = municipal.collect_sb_unity_program_categories(
        _target(), timeout=7, max_pages=1, detail_limit=4
    )
    by_title = {row["title"]: row for row in rows}

    assert set(by_title) == {"상세 마감", "상세 접수", "상세 운영"}
    assert by_title["상세 마감"]["status"] == "접수마감"
    assert by_title["상세 마감"]["reservation_available"] is False
    assert not by_title["상세 마감"]["application_url"]
    assert not by_title["상세 마감"]["application_type"]
    assert by_title["상세 접수"]["status"] == "접수중"
    assert by_title["상세 접수"]["reservation_available"] is True
    assert by_title["상세 접수"]["application_url"] == by_title["상세 접수"]["raw_url"]
    assert by_title["상세 접수"]["application_type"] == "ONLINE_RESERVATION"
    assert by_title["상세 운영"]["status"] == "운영중"
    assert by_title["상세 운영"]["reservation_available"] is False
    assert meta["ended_rows"] == 1
    assert meta["detail_pages"] == 4


def test_sb_detail_limit_does_not_mark_list_collection_incomplete(monkeypatch) -> None:
    fake_fetch, _fetched = _fake_fetch_factory()
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    rows, _parser, meta = municipal.collect_sb_unity_program_categories(
        _target(), timeout=7, max_pages=1, detail_limit=5
    )

    assert len(rows) == 20
    assert meta["detail_pages"] == 5
    assert meta["detail_enrichment_capped"] is True
    assert meta["pagination_complete"] is True
    assert meta["pagination_exhausted"] is True
    assert "configured_collection_error" not in meta


def test_sb_target_params_follow_each_configured_target_query() -> None:
    assert municipal.sb_target_params(SB_TARGET_URL) == {
        "key": "6950",
        "insttTy": "URINTY01",
        "searchInsttNo": "54",
        "viewType": "list",
        "pageUnit": "100",
    }
    assert municipal.sb_target_params(
        "https://www.sb.go.kr/yeyak/selectUnityProgrmWebList.do?"
        "key=6508&insttTy=URINTY01&searchInsttNo=84&searchDong=%EC%A2%85%EC%95%94%EB%8F%99"
    )["searchInsttNo"] == "84"

    with pytest.raises(ValueError, match="official HTTPS"):
        municipal.sb_target_params(
            "http://www.sb.go.kr/yeyak/selectUnityProgrmWebList.do?"
            "key=6950&insttTy=URINTY01&searchInsttNo=54"
        )
    with pytest.raises(ValueError, match="insttTy"):
        municipal.sb_target_params(
            "https://www.sb.go.kr/yeyak/selectUnityProgrmWebList.do?"
            "key=6950&insttTy=OTHER&searchInsttNo=54"
        )


def test_existing_sb_integrated_reservation_target_is_locked_to_education() -> None:
    target_path = Path(__file__).resolve().parents[1] / "config" / "crawl_targets" / "public_reservation.yaml"
    document = yaml.safe_load(target_path.read_text(encoding="utf-8"))
    row = next(item for item in document["targets"] if item.get("provider") == SB_EXISTING_PROVIDER)

    assert row["service_group"] == "공공강좌"
    assert row["service_group_policy"] == "locked"
