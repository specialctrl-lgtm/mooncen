from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as municipal


ULJU_PROVIDER = "MUNI_CRS_ULJUSISEOL_OR_KR_DC828DD7"


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _root(path: str, facilities: dict[str, tuple[str, str]]) -> BeautifulSoup:
    links = "".join(
        f'<a href="{path}?mem_id={code}">{name}</a>'
        for code, (name, _address) in facilities.items()
    )
    return _soup(f"<html><body>{links}</body></html>")


def _row(path: str, lecture_id: str, branch_code: str, title: str, period: str, status: str) -> str:
    return f"""
    <tr>
      <td class="subject">
        <a href="{path}?mem_id={branch_code}&amp;selcheck=&amp;lec_id={lecture_id}&amp;prc=detail&amp;pg=9">
          <p class="tit">{title}</p>
        </a>
        <p><span>강습기간</span>{period}</p>
        <p><span>강습시간</span>목 10:00~11:00</p>
      </td>
      <td class="devide">문화강좌</td>
      <td class="pay">무료</td>
      <td class="state">{status}</td>
    </tr>
    """


def _list_page(path: str, branch_code: str, index: int) -> BeautifulSoup:
    rows = [
        _row(
            path,
            f"L900{index}",
            branch_code,
            f"공식 강좌 {index}",
            "2099.08.01~2099.08.31",
            "대기자접수" if branch_code == "B0000192" else "접수중",
        )
    ]
    tests = {
        "B0000189": ("L9111", "*테스트 페이지_접수하지마세요!!"),
        "B0000188": ("L9112", "긴급점검 테스트 예약금지"),
        "B0000199": ("L9113", "테스트(등록 금지)"),
    }
    if branch_code in tests:
        lecture_id, title = tests[branch_code]
        rows.append(
            _row(path, lecture_id, branch_code, title, "2099.08.01~2099.08.31", "준비중")
        )
    if branch_code == "B0000191":
        rows.append(
            _row(path, "L9199", branch_code, "종료 강좌", "2020.01.01~2020.01.31", "강습종료")
        )
    return _soup(f'<html><body><table class="table_list"><tbody>{"".join(rows)}</tbody></table></body></html>')


def _detail_page(status: str = "접수중") -> BeautifulSoup:
    return _soup(
        """
        <html><body>
          <div class="info_area">
            <div class="s_tit2">공식 강의실</div>
            <ul><li>주소 :</li><li>문의전화 : 052-229-0000</li></ul>
          </div>
          <table class="table_st2"><tbody>
            <tr><th>강습대상</th><td>울주군민</td></tr>
            <tr><th>정원</th><td>20명</td></tr>
            <tr><th>신규회원모집기간</th><td>2099.07.01~2099.07.20</td></tr>
            <tr><th>강습기간</th><td>2099.08.01~2099.08.31</td></tr>
            <tr><th>강습시간</th><td>목 10:00~11:00</td></tr>
            <tr><th>수강료</th><td><span class="t_orange">10,000원</span></td></tr>
            <tr><th>강사</th><td>김강사</td></tr>
          </tbody></table>
          <div class="tab_cont" id="tab2">공식 강좌 설명</div>
          <p>{status}</p>
        </body></html>
        """.format(status=status)
    )


def _target(provider: str = ULJU_PROVIDER, url: str | None = None) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=provider,
        name="울주군공공시설예약서비스",
        branch="울산광역시 울주군",
        url=url or "https://crs.uljusiseol.or.kr/new_lecture/lecture",
        source="test",
        priority=1,
        region="울산광역시 울주군",
        extra={"domain_category": "교육·강좌"},
    )


def test_ulju_dispatch_collects_all_facilities_with_stable_ids_and_official_addresses(monkeypatch) -> None:
    fetched: list[str] = []
    facilities = municipal.ULJU_PUBLIC_FACILITIES
    indexes = {code: index for index, code in enumerate(facilities, start=1)}

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if query.get("prc") == ["detail"]:
            assert set(query) == {"prc", "lec_id", "mem_id"}
            return _detail_page()
        branch_code = (query.get("mem_id") or [""])[0]
        if branch_code:
            return _list_page(parsed.path, branch_code, indexes[branch_code])
        return _root(parsed.path, facilities)

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    rows, parser, meta = municipal.collect_from_url(
        _target(), timeout=7, max_depth=0, max_pages=7, detail_limit=7
    )

    assert parser == "ulsan_bukgu_lecture_list+detail"
    assert len(rows) == 7
    assert len({row["provider_course_id"] for row in rows}) == 7
    assert len({row["raw_url"] for row in rows}) == 7
    by_code = {row["branch_code"]: row for row in rows}
    for code, (branch, address) in facilities.items():
        row = by_code[code]
        lecture_id = row["raw_fields"]["lecture_id"]
        assert row["provider_course_id"] == f"{ULJU_PROVIDER}:lecture:{lecture_id}"
        assert row["branch"] == branch
        assert row["preserve_branch"] is True
        assert row["address"] == address
        assert row["venue_address"] == address
        assert row["room"] == "공식 강의실"
        assert row["target"] == "울주군민"
        assert row["capacity"] == "20명"
        assert parse_qs(urlparse(row["raw_url"]).query) == {
            "prc": ["detail"],
            "lec_id": [lecture_id],
            "mem_id": [code],
        }

    assert by_code["B0000192"]["reservation_available"] is True
    assert meta["pages"] == 7
    assert meta["facility_count"] == 7
    assert meta["facilities_processed"] == 7
    assert meta["discovered_links"] == 11
    assert meta["test_rows"] == 3
    assert meta["expired_rows"] == 0
    assert meta["ended_rows"] == 1
    assert meta["detail_pages"] == 7
    assert meta["detail_candidates"] == 7
    assert meta["detail_enrichment_capped"] is False
    assert meta["pagination_complete"] is True
    assert meta["no_current_data"] is False
    assert len(fetched) == 15


def test_ulju_facility_cap_is_reported_as_partial(monkeypatch) -> None:
    facilities = municipal.ULJU_PUBLIC_FACILITIES
    indexes = {code: index for index, code in enumerate(facilities, start=1)}

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        branch_code = (parse_qs(parsed.query).get("mem_id") or [""])[0]
        return _list_page(parsed.path, branch_code, indexes[branch_code]) if branch_code else _root(parsed.path, facilities)

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    rows, _parser, meta = municipal.collect_ulsan_bukgu_lecture(
        _target(), timeout=5, max_pages=6, detail_limit=0
    )

    assert len(rows) == 6
    assert meta["pagination_complete"] is False
    assert meta["no_current_data"] is False
    assert meta["configured_collection_error"] == "facility fanout capped at 6 of 7"


def test_ulju_only_exposes_application_fields_for_accepting_statuses(monkeypatch) -> None:
    branch_code = "B0000191"
    branch_name, _address = municipal.ULJU_PUBLIC_FACILITIES[branch_code]

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        if parse_qs(parsed.query).get("mem_id"):
            rows = [
                _row(parsed.path, "L9301", branch_code, "접수 강좌", "2099.01.01~2099.12.31", "접수중"),
                _row(parsed.path, "L9302", branch_code, "마감 강좌", "2099.01.01~2099.12.31", "접수마감"),
                _row(parsed.path, "L9303", branch_code, "운영 강좌", "2099.01.01~2099.12.31", "운영중"),
                _row(parsed.path, "L9304", branch_code, "종료 강좌", "2099.01.01~2099.12.31", "강습종료"),
                _row(parsed.path, "L9305", branch_code, "폐강 강좌", "2099.01.01~2099.12.31", "폐강"),
            ]
            return _soup(
                f'<table class="table_list"><tbody>{"".join(rows)}</tbody></table>'
            )
        return _root(parsed.path, {branch_code: (branch_name, "")})

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    rows, _parser, meta = municipal.collect_ulsan_bukgu_lecture(
        _target(), timeout=5, max_pages=1, detail_limit=0
    )
    by_status = {row["status"]: row for row in rows}

    assert by_status["접수중"]["reservation_available"] is True
    assert by_status["접수중"]["application_url"]
    assert by_status["접수중"]["application_type"] == "ONLINE_RESERVATION"
    for status in ("접수마감", "운영중"):
        assert by_status[status]["reservation_available"] is False
        assert not by_status[status]["application_url"]
        assert not by_status[status]["application_type"]
    assert meta["reservation_discovery_links"] == 1
    assert meta["ended_rows"] == 2
    assert meta["invalid_count"] == 0


def test_ulju_official_fanout_treats_explicit_no_data_as_clean_empty(monkeypatch) -> None:
    fetched_codes: list[str] = []

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        branch_code = (parse_qs(parsed.query).get("mem_id") or [""])[0]
        if branch_code:
            fetched_codes.append(branch_code)
            return _soup(
                '<table class="table_list"><tbody><tr><td colspan="8">등록된 강좌가 없습니다.</td></tr></tbody></table>'
            )
        first_code = next(iter(municipal.ULJU_PUBLIC_FACILITIES))
        return _root(parsed.path, {first_code: municipal.ULJU_PUBLIC_FACILITIES[first_code]})

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    rows, _parser, meta = municipal.collect_ulsan_bukgu_lecture(
        _target(), timeout=5, max_pages=7, detail_limit=0
    )

    assert rows == []
    assert set(fetched_codes) == set(municipal.ULJU_PUBLIC_FACILITIES)
    assert meta["facility_fanout_source"] == "official_mapping"
    assert len(meta["facility_codes_missing"]) == 6
    assert meta["no_data_placeholders"] == 7
    assert meta["valid_count"] == 0
    assert meta["invalid_count"] == 0
    assert meta["pagination_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"] == "explicit no-data placeholder"
    assert "configured_collection_error" not in meta


def test_ulju_all_malformed_rows_report_collection_error(monkeypatch) -> None:
    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        if parse_qs(parsed.query).get("mem_id"):
            return _soup(
                '<table class="table_list"><tbody><tr><td class="subject"><p class="tit">깨진 강좌</p></td></tr></tbody></table>'
            )
        return _soup("<html><body></body></html>")

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    rows, _parser, meta = municipal.collect_ulsan_bukgu_lecture(
        _target(), timeout=5, max_pages=7, detail_limit=0
    )

    assert rows == []
    assert meta["valid_count"] == 0
    assert meta["invalid_count"] == 7
    assert meta["no_data_placeholders"] == 0
    assert meta["pagination_complete"] is False
    assert meta["no_current_data"] is False
    assert "all listed lecture rows were malformed" in meta["configured_collection_error"]


def test_ulju_detail_status_renormalizes_application_and_filters_ended(monkeypatch) -> None:
    branch_code = next(iter(municipal.ULJU_PUBLIC_FACILITIES))
    statuses = {"L9401": "접수마감", "L9402": "접수중", "L9403": "강습종료"}

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if query.get("prc") == ["detail"]:
            return _detail_page(statuses[query["lec_id"][0]])
        current_code = (query.get("mem_id") or [""])[0]
        if current_code == branch_code:
            rows = [
                _row(parsed.path, "L9401", current_code, "상세 마감", "2099.01.01~2099.12.31", "접수중"),
                _row(parsed.path, "L9402", current_code, "상세 접수", "2099.01.01~2099.12.31", "접수마감"),
                _row(parsed.path, "L9403", current_code, "상세 종료", "2099.01.01~2099.12.31", "접수중"),
            ]
            return _soup(f'<table class="table_list"><tbody>{"".join(rows)}</tbody></table>')
        if current_code:
            return _soup(
                '<table class="table_list"><tbody><tr><td colspan="8">등록된 강좌가 없습니다.</td></tr></tbody></table>'
            )
        return _soup("<html><body></body></html>")

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    rows, _parser, meta = municipal.collect_ulsan_bukgu_lecture(
        _target(), timeout=5, max_pages=7, detail_limit=3
    )
    by_title = {row["title"]: row for row in rows}

    assert set(by_title) == {"상세 마감", "상세 접수"}
    assert by_title["상세 마감"]["status"].startswith("접수마감")
    assert by_title["상세 마감"]["reservation_available"] is False
    assert not by_title["상세 마감"]["application_url"]
    assert not by_title["상세 마감"]["application_type"]
    assert by_title["상세 접수"]["status"].startswith("접수중")
    assert by_title["상세 접수"]["reservation_available"] is True
    assert by_title["상세 접수"]["application_url"] == by_title["상세 접수"]["raw_url"]
    assert by_title["상세 접수"]["application_type"] == "ONLINE_RESERVATION"
    assert meta["ended_rows"] == 1
    assert meta["detail_candidates"] == 3
    assert meta["detail_pages"] == 3
    assert meta["detail_enrichment_capped"] is False


def test_ulju_detail_fee_uses_only_the_tuition_header_value(monkeypatch) -> None:
    pages = iter(
        [
            _soup(
                '<table class="table_st2"><tr><th>정원</th><td><span class="t_orange">20명</span></td></tr></table>'
            ),
            _soup(
                '<table class="table_st2"><tr><th>정원</th><td>20명</td></tr>'
                '<tr><th>수강료</th><td><span class="t_orange">10,000원</span></td></tr></table>'
            ),
        ]
    )
    monkeypatch.setattr(municipal, "fetch_soup", lambda *_args, **_kwargs: next(pages))

    without_fee = municipal.collect_ulsan_bukgu_detail(object(), "https://example.test/1", 5)
    with_fee = municipal.collect_ulsan_bukgu_detail(object(), "https://example.test/2", 5)

    assert without_fee["fee"] == ""
    assert with_fee["fee"] == "10,000원"


def test_ulsan_public_detail_url_uses_only_lecture_and_facility_identity() -> None:
    first = municipal.canonical_ulsan_public_lecture_detail_url(
        "https://crs.uljusiseol.or.kr/new_lecture/lecture?mem_id=B0000191",
        "/new_lecture/lecture?pg=9&mem_id=B0000191&lec_id=L0117752&prc=detail&selcheck=x#tab",
    )
    second = municipal.canonical_ulsan_public_lecture_detail_url(
        "https://crs.uljusiseol.or.kr/new_lecture/lecture?mem_id=B0000191",
        "/new_lecture/lecture?prc=detail&lec_id=L0117752&mem_id=B0000191",
    )

    assert first == second == (
        "https://crs.uljusiseol.or.kr/new_lecture/lecture?prc=detail&lec_id=L0117752&mem_id=B0000191",
        "L0117752",
        "B0000191",
    )


def test_existing_ulsan_bukgu_route_uses_dedicated_collector(monkeypatch) -> None:
    root_url = "https://crs.ubimc.or.kr/yeyak/new_lecture/lecture"
    sentinel_rows = [
        {
            "provider": "ULSAN_BUKGU_PUBLIC_RESERVATION",
            "provider_course_id": "ULSAN_BUKGU_PUBLIC_RESERVATION:lecture:L9001",
            "branch": "북구센터",
            "address": "",
        }
    ]
    captured: dict[str, object] = {}

    def fake_collect(target, **kwargs):
        captured["provider"] = target.provider
        captured.update(kwargs)
        return (
            sentinel_rows,
            municipal.municipal_ulsan_bukgu.ULSAN_BUKGU_PUBLIC_PARSER,
            {"snapshot_complete": True},
        )

    monkeypatch.setattr(
        municipal.municipal_ulsan_bukgu,
        "collect_ulsan_bukgu_courses",
        fake_collect,
    )

    rows, parser, meta = municipal.collect_from_url(
        _target("ULSAN_BUKGU_PUBLIC_RESERVATION", root_url),
        timeout=5,
        max_depth=0,
        max_pages=2,
        detail_limit=0,
    )

    assert parser == municipal.municipal_ulsan_bukgu.ULSAN_BUKGU_PUBLIC_PARSER
    assert rows == sentinel_rows
    assert captured["provider"] == "ULSAN_BUKGU_PUBLIC_RESERVATION"
    assert rows[0]["branch"] == "북구센터"
    assert rows[0]["address"] == ""
    assert rows[0]["provider_course_id"] == "ULSAN_BUKGU_PUBLIC_RESERVATION:lecture:L9001"
    assert meta["snapshot_complete"] is True
