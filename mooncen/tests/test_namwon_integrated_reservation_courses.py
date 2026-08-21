from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_namwon as namwon


@dataclass
class Target:
    provider: str = namwon.NAMWON_PROVIDER
    url: str = namwon.NAMWON_URL


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _uid(seed: int) -> str:
    return f"{seed:032x}"


def _tags(*, lecture: bool = False) -> list[dict[str, Any]]:
    result = [
        {
            "tagUid": _uid(900),
            "tagGroup": {"groupId": "LECTURE_CATEGORY"},
            "tagName": "생활교육",
        }
    ]
    if lecture:
        result.append(
            {
                "tagUid": namwon._LECTURE_TAG_UID,
                "tagGroup": {"groupId": "SHOWLECTURE"},
                "tagName": "강좌",
            }
        )
    return result


def _item(
    source: namwon.NamwonSource,
    seed: int,
    title: str,
    *,
    begin: str,
    end: str,
    progress: str,
    apply_begin: str = "2026-06-01 09:00",
    apply_end: str = "2026-06-30 18:00",
) -> dict[str, Any]:
    facility_seed = {"EDU": 100, "LIFELONG": 101, "FESTIVAL": 102}[source.code]
    return {
        "itemUid": _uid(seed),
        "itemTitle": title,
        "instUid": _uid(80),
        "fcltUid": _uid(facility_seed),
        "explanation": f"{title} 공식 설명",
        "maxCapacity": 10,
        "baseCapacity": 1,
        "waitCapacity": 2,
        "applyCount": 3,
        "waitCount": 0,
        "baseFee": 0,
        "itemAddr": "전북특별자치도 남원시 요천로 1",
        "itemDetailAddr": source.facility_name,
        "facilityInfo": {
            "fcltUid": _uid(facility_seed),
            "fcltName": source.facility_name,
            "fcltCode": source.code,
            "instUid": _uid(80),
            "rsvtType": "EDUCATION",
            "rsvtMthd": "FCFS",
        },
        "tags": _tags(lecture=bool(source.required_tag_uid)),
        "applyBeginDate": apply_begin,
        "applyEndDate": apply_end,
        "beginDate": begin,
        "endDate": end,
        "timeInfo": "매주 화 10:00~12:00",
        "itemInfo1": "홍길동",
        "itemInfo2": "063-620-0000",
        "itemInfo3": "교육실",
        "itemInfo4": "남원시민",
        "useWaiting": True,
        "itemProgress": progress,
        "itemApplyCountType": "COUNT",
    }


def _default_rows() -> dict[str, list[dict[str, Any]]]:
    edu, lifelong, _festival = namwon.NAMWON_SOURCES
    return {
        "EDU": [
            _item(
                edu,
                1,
                "현재 시민교육",
                begin="2026-08-01",
                end="2026-08-31",
                progress="PROCEEDING",
            ),
            _item(
                edu,
                2,
                "과거 시민교육 1",
                begin="2025-01-01",
                end="2025-02-01",
                progress="DEADLINE",
                apply_begin="2024-12-01 09:00",
                apply_end="2024-12-31 18:00",
            ),
            _item(
                edu,
                3,
                "과거 시민교육 2",
                begin="2024-01-01",
                end="2024-02-01",
                progress="DEADLINE",
                apply_begin="2023-12-01 09:00",
                apply_end="2023-12-31 18:00",
            ),
        ],
        "LIFELONG": [
            _item(
                lifelong,
                4,
                "현재 평생교육",
                begin="2026-07-20",
                end="2026-09-30",
                progress="DEADLINE",
            )
        ],
        "FESTIVAL": [],
    }


def _root_html() -> str:
    return """
    <html><head><title>통합예약 | 남원시</title></head><body>
      <h1>남원시 통합예약포털</h1>
      <a href="?menuUid=1">시민참여교육</a>
      <a href="?menuUid=2">평생학습관</a>
      <a href="?menuUid=3">공연·강좌 신청</a>
    </body></html>
    """


def _landing_html(source: namwon.NamwonSource) -> str:
    title = "공연·강좌" if source.code == "FESTIVAL" else source.label
    lecture = (
        f'<input name="tags1" value="{source.required_tag_uid}">'
        if source.required_tag_uid
        else ""
    )
    return f"""
    <html><head><title>{title} | 남원시 통합예약</title></head><body>
      <input id="rsvtType" value="EDUCATION">
      <input id="fcltCodes" value="{source.code}">
      <input id="nextMenuUid" value="{source.detail_menu_uid}">
      <input id="sort" value="registerDt,desc">
      {lecture}
    </body></html>
    """


def _payload(items: list[dict[str, Any]], *, page: int, total: int) -> dict[str, Any]:
    size = namwon.NAMWON_API_PAGE_SIZE
    pages = (total + size - 1) // size if total else 0
    return {
        "result": {
            "content": deepcopy(items),
            "number": page - 1,
            "size": size,
            "numberOfElements": len(items),
            "totalElements": total,
            "totalPages": pages,
            "first": page == 1,
            "last": pages == 0 or page >= pages,
            "empty": not items,
            "pageable": {
                "pageNumber": page - 1,
                "pageSize": size,
                "offset": (page - 1) * size,
            },
        }
    }


def _detail_html(
    source: namwon.NamwonSource,
    item: dict[str, Any],
    *,
    title: str | None = None,
    status: str | None = None,
    list_uid: str | None = None,
    identity: str | None = None,
) -> str:
    source_status = item["itemProgress"]
    display_status = status or {
        "PROCEEDING": "접수중",
        "DEADLINE": "접수마감",
        "SCHEDULED": "진행예정",
    }[source_status]
    action = "신청하기" if display_status == "접수중" else (
        "진행예정" if display_status == "진행예정" else "접수마감"
    )
    page_title = "공연·강좌" if source.code == "FESTIVAL" else source.label
    marker_identity = identity or item["itemUid"]
    pairs = {
        "기관": source.facility_name,
        "접수": f"{item['applyBeginDate']} ~ {item['applyEndDate']}",
        "강사명": item["itemInfo1"],
        "일자": f"{item['beginDate']} ~ {item['endDate']}",
        "수강료": "무료",
        "시간": item["timeInfo"],
        "교육대상": item["itemInfo4"],
        "장소": item["itemInfo3"],
    }
    fields = "".join(
        f"<li><strong>{key}</strong><p>{value}</p></li>"
        for key, value in pairs.items()
    )
    return f"""
    <html><head><title>{page_title} 상세 | 남원시 통합예약</title></head><body>
      <div class="txt_area">
        <div class="cate_box"><span class="cate">생활교육</span><span class="status">{display_status}</span></div>
        <div class="tit_area"><strong>{title or item['itemTitle']}</strong></div>
        <ul class="info_list">{fields}</ul>
        <div class="btn_area">
          <ul class="info"><li><span class="num">{item['applyCount']}<i>/ {item['maxCapacity']}</i></span></li></ul>
          <a class="button">{action}</a>
        </div>
      </div>
      <a href="/reserve/login.do?returnUrl=%2Freserve%2Findex.do%3FitemUid%3D{marker_identity}">로그인</a>
      <a class="btn_list" href="/reserve/index.do?menuUid={list_uid or source.list_menu_uid}">목록</a>
      <div id="detailTab01">{item['itemTitle']} 상세 안내</div>
    </body></html>
    """


PayloadHook = Callable[[str, int, int, dict[str, Any]], dict[str, Any]]
DetailHook = Callable[[namwon.NamwonSource, dict[str, Any]], str]


class FixtureSite:
    def __init__(
        self,
        *,
        rows: dict[str, list[dict[str, Any]]] | None = None,
        payload_hook: PayloadHook | None = None,
        detail_hook: DetailHook | None = None,
    ) -> None:
        self.rows = deepcopy(rows or _default_rows())
        self.payload_hook = payload_hook
        self.detail_hook = detail_hook
        self.calls: list[str] = []
        self.api_calls: dict[tuple[str, int], int] = {}
        self.sessions: list[FakeSession] = []

    def session_factory(self) -> FakeSession:
        session = FakeSession()
        self.sessions.append(session)
        return session

    def fetcher(self, _session: Any, url: str, _timeout: int) -> BeautifulSoup:
        self.calls.append(url)
        if url == namwon.NAMWON_ROOT_URL:
            return BeautifulSoup(_root_html(), "lxml")
        for source in namwon.NAMWON_SOURCES:
            if url == namwon.namwon_landing_url(source):
                return BeautifulSoup(_landing_html(source), "lxml")
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == "/reserve/index.do" and "itemUid" in query:
            identity = query["itemUid"][0]
            for source in namwon.NAMWON_SOURCES:
                for item in self.rows[source.code]:
                    if item["itemUid"] == identity:
                        html = (
                            self.detail_hook(source, item)
                            if self.detail_hook
                            else _detail_html(source, item)
                        )
                        return BeautifulSoup(html, "lxml")
        raise AssertionError(f"unexpected HTML URL: {url}")

    def json_getter(
        self,
        _session: Any,
        url: str,
        params: dict[str, Any],
        _timeout: int,
    ) -> dict[str, Any]:
        assert url == namwon.NAMWON_API_URL
        code = str(params["fcltCodes"])
        page = int(params["page"])
        assert params["rsvtType"] == "EDUCATION"
        assert params["sort"] == "registerDt,desc"
        assert int(params["size"]) == namwon.NAMWON_API_PAGE_SIZE
        source = next(item for item in namwon.NAMWON_SOURCES if item.code == code)
        assert params.get("tagUids", "") == source.required_tag_uid
        key = (code, page)
        call = self.api_calls.get(key, 0) + 1
        self.api_calls[key] = call
        all_rows = self.rows[code]
        size = namwon.NAMWON_API_PAGE_SIZE
        selected = all_rows[(page - 1) * size : page * size]
        result = _payload(selected, page=page, total=len(all_rows))
        return self.payload_hook(code, page, call, result) if self.payload_hook else result


def _collect(monkeypatch: pytest.MonkeyPatch, site: FixtureSite, **kwargs: Any):
    monkeypatch.setattr(namwon, "NAMWON_API_PAGE_SIZE", 2)
    return namwon.collect_namwon_education_courses(
        Target(),
        timeout=2,
        max_pages=kwargs.pop("max_pages", 10),
        detail_limit=kwargs.pop("detail_limit", 100),
        today=kwargs.pop("today", "2026-07-20"),
        max_workers=kwargs.pop("max_workers", 1),
        fetcher=site.fetcher,
        json_getter=site.json_getter,
        session_factory=site.session_factory,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        ("WRONG", namwon.NAMWON_URL),
        (namwon.NAMWON_PROVIDER, "http://www.namwon.go.kr/reserve"),
        (namwon.NAMWON_PROVIDER, "https://www.namwon.go.kr/reserve/"),
        (namwon.NAMWON_PROVIDER, "https://www.namwon.go.kr:443/reserve"),
        (namwon.NAMWON_PROVIDER, "https://www.namwon.go.kr:bad/reserve"),
        (namwon.NAMWON_PROVIDER, "https://user@www.namwon.go.kr/reserve"),
        (namwon.NAMWON_PROVIDER, "https://www.namwon.go.kr/reserve?x=1"),
        (namwon.NAMWON_PROVIDER, "https://www.namwon.go.kr.evil.test/reserve"),
    ],
)
def test_exact_target_boundary(provider: str, url: str) -> None:
    assert not namwon.is_target(Target(provider=provider, url=url))
    assert namwon.is_target(Target())


def test_reviewed_constants_and_url_builders() -> None:
    assert namwon.NAMWON_CANDIDATE_ID == "MUNI_IR_A69D8582681A"
    assert namwon.NAMWON_MUNICIPALITY_CODE == "5219000000"
    assert namwon.NAMWON_MUNICIPALITY_NAME == "전북특별자치도 남원시"
    assert namwon.NAMWON_ROOT_URL == "https://www.namwon.go.kr/reserve/index.do"
    assert namwon.namwon_api_params("FESTIVAL", 2) == {
        "rsvtType": "EDUCATION",
        "fcltCodes": "FESTIVAL",
        "page": 2,
        "size": 500,
        "sort": "registerDt,desc",
        "tagUids": namwon._LECTURE_TAG_UID,
    }
    assert namwon.namwon_detail_url("EDU", "bad&item=1") == ""
    detail = namwon.namwon_detail_url("EDU", _uid(1))
    assert parse_qs(urlparse(detail).query) == {
        "menuUid": [namwon.NAMWON_SOURCES[0].detail_menu_uid],
        "itemUid": [_uid(1)],
        "historyPage": ["1"],
    }


def test_complete_snapshot_exhausts_pages_and_enriches_every_current_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = FixtureSite()
    rows, parser, meta = _collect(monkeypatch, site)

    assert parser == namwon.NAMWON_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["source_rows"] == 4
    assert meta["source_totals"] == {"EDU": 3, "LIFELONG": 1, "FESTIVAL": 0}
    assert meta["source_page_counts"] == {"EDU": 2, "LIFELONG": 1, "FESTIVAL": 0}
    assert meta["data_pages"] == 4
    assert meta["sentinel_requests"] == 2
    assert meta["page_one_rechecks"] == 3
    assert meta["api_requests"] == 9
    assert meta["pages"] == 9
    assert meta["request_count"] == 15
    assert meta["expired_count"] == 2
    assert meta["current_count"] == meta["returned_count"] == 2
    assert {row["title"] for row in rows} == {"현재 시민교육", "현재 평생교육"}
    assert {row["status"] for row in rows} == {"OPEN", "CLOSED"}
    assert sum(bool(row["application_url"]) for row in rows) == 1
    assert all(row["domain_category"] == "교육" for row in rows)
    assert all(row["fee"] is not None for row in rows)
    assert all(session.closed for session in site.sessions)


def test_full_primary_capacity_stays_open_while_official_waitlist_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows_by_source = _default_rows()
    current = rows_by_source["EDU"][0]
    current["maxCapacity"] = 10
    current["applyCount"] = 10
    current["waitCapacity"] = 2
    current["waitCount"] = 1
    current["useWaiting"] = True

    rows, _, meta = _collect(
        monkeypatch,
        FixtureSite(rows=rows_by_source),
    )

    assert meta["snapshot_complete"] is True
    row = next(
        item
        for item in rows
        if item["raw_fields"]["item_uid"] == current["itemUid"]
    )
    assert row["status"] == "OPEN"
    assert row["capacity_remaining"] == 0
    assert row["waitlist_current"] == 1
    assert row["waitlist_total"] == 2
    assert row["reservation_available"] is True


def test_page_and_detail_caps_fail_before_detail_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = FixtureSite()
    rows, _, meta = _collect(monkeypatch, site, max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "below declared 2 pages" in meta["configured_collection_error"]
    assert not any("itemUid=" in url for url in site.calls)

    site = FixtureSite()
    rows, _, meta = _collect(monkeypatch, site, detail_limit=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["detail_attempts"] == 0
    assert "below required 2 details" in meta["configured_collection_error"]


def test_nonempty_overrun_and_page_one_change_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def nonempty_sentinel(code: str, page: int, _call: int, payload: dict[str, Any]):
        if code == "EDU" and page == 3:
            payload["result"]["content"] = [deepcopy(_default_rows()["EDU"][0])]
            payload["result"]["numberOfElements"] = 1
            payload["result"]["empty"] = False
        return payload

    rows, _, meta = _collect(monkeypatch, FixtureSite(payload_hook=nonempty_sentinel))
    assert rows == []
    assert "overrun page is not empty" in meta["configured_collection_error"]

    def changed_page_one(code: str, page: int, call: int, payload: dict[str, Any]):
        if code == "EDU" and page == 1 and call == 2:
            payload["result"]["content"][0]["itemTitle"] = "동시 변경된 제목"
        return payload

    rows, _, meta = _collect(monkeypatch, FixtureSite(payload_hook=changed_page_one))
    assert rows == []
    assert "page one changed during complete traversal" in meta["configured_collection_error"]


def test_cross_source_identity_and_nonlecture_rows_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows_by_source = _default_rows()
    rows_by_source["LIFELONG"][0]["itemUid"] = rows_by_source["EDU"][0]["itemUid"]
    rows, _, meta = _collect(monkeypatch, FixtureSite(rows=rows_by_source))
    assert rows == []
    assert "duplicate itemUid across education sources" in meta["configured_collection_error"]

    rows_by_source = _default_rows()
    festival = namwon.NAMWON_SOURCES[2]
    festival_row = _item(
        festival,
        20,
        "태그 없는 공연",
        begin="2026-08-01",
        end="2026-08-02",
        progress="PROCEEDING",
    )
    festival_row["tags"] = _tags(lecture=False)
    rows_by_source["FESTIVAL"] = [festival_row]
    rows, _, meta = _collect(monkeypatch, FixtureSite(rows=rows_by_source))
    assert rows == []
    assert "is not a lecture" in meta["configured_collection_error"]


@pytest.mark.parametrize("mode", ["title", "status", "list", "identity"])
def test_any_current_detail_contract_change_discards_the_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    def changed_detail(source: namwon.NamwonSource, item: dict[str, Any]) -> str:
        if item["itemUid"] != _uid(1):
            return _detail_html(source, item)
        kwargs: dict[str, str] = {}
        if mode == "title":
            kwargs["title"] = "변경된 제목"
        elif mode == "status":
            kwargs["status"] = "접수마감"
        elif mode == "list":
            kwargs["list_uid"] = _uid(999)
        elif mode == "identity":
            kwargs["identity"] = _uid(999)
        return _detail_html(source, item, **kwargs)

    rows, _, meta = _collect(
        monkeypatch,
        FixtureSite(detail_hook=changed_detail),
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "detail" in meta["configured_collection_error"]


@pytest.mark.parametrize("anomaly", ["reversed_dates", "negative_wait"])
def test_exact_provably_expired_source_defects_are_accounted_but_never_published(
    monkeypatch: pytest.MonkeyPatch,
    anomaly: str,
) -> None:
    rows_by_source = _default_rows()
    historical = rows_by_source["EDU"][1]
    if anomaly == "reversed_dates":
        historical["beginDate"] = "2025-02-01"
        historical["endDate"] = "2025-01-01"
    else:
        historical["waitCount"] = -1

    rows, _, meta = _collect(monkeypatch, FixtureSite(rows=rows_by_source))
    assert len(rows) == 2
    assert meta["snapshot_complete"] is True
    assert meta["source_rows"] == meta["validated_count"] == 4
    assert meta["valid_count"] == 3
    assert meta["historical_invalid_count"] == 1
    assert meta["historical_invalid_ids"] == [historical["itemUid"]]
    assert historical["itemUid"] not in {
        row["raw_fields"]["item_uid"] for row in rows
    }


@pytest.mark.parametrize("anomaly", ["reversed_dates", "negative_wait"])
def test_the_same_defects_are_not_accepted_for_current_or_future_rows(
    monkeypatch: pytest.MonkeyPatch,
    anomaly: str,
) -> None:
    rows_by_source = _default_rows()
    current = rows_by_source["EDU"][0]
    if anomaly == "reversed_dates":
        current["beginDate"] = "2026-09-01"
        current["endDate"] = "2026-08-01"
    else:
        current["waitCount"] = -1
    rows, _, meta = _collect(monkeypatch, FixtureSite(rows=rows_by_source))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "reversed" in meta["configured_collection_error"] or "sentinel" in meta[
        "configured_collection_error"
    ]


def test_complete_all_expired_snapshot_is_a_valid_empty_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows_by_source = _default_rows()
    for item in rows_by_source["EDU"] + rows_by_source["LIFELONG"]:
        item["beginDate"] = "2025-01-01"
        item["endDate"] = "2025-02-01"
        item["applyBeginDate"] = "2024-12-01 09:00"
        item["applyEndDate"] = "2024-12-31 18:00"
        item["itemProgress"] = "DEADLINE"
    site = FixtureSite(rows=rows_by_source)
    rows, _, meta = _collect(monkeypatch, site)
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["current_count"] == meta["detail_attempts"] == 0


def test_dedupe_may_not_remove_a_valid_current_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, _, meta = _collect(
        monkeypatch,
        FixtureSite(),
        dedupe_rows=lambda values: values[:-1],
    )
    assert rows == []
    assert "dedupe changed complete row count 2 to 1" in meta[
        "configured_collection_error"
    ]
