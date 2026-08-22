from __future__ import annotations

import html
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

import Crawler.Crawler_MunicipalYaml as municipal


PROVIDER = municipal.POCHEON_GGSHARE_PROVIDER
TARGET_URL = municipal.POCHEON_GGSHARE_LIST_URL


def _target(provider: str = PROVIDER, url: str = TARGET_URL) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=provider,
        name="포천시 통합예약 교육강좌",
        branch="경기도 포천시",
        url=url,
        source="test",
        region="경기도",
        extra={
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "source_group": "municipal_reservation",
            "operator_type": "지자체/공공기관",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
        },
    )


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


class _SessionMarker:
    def __init__(self, label: str) -> None:
        self.label = label
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _receipt(item: dict[str, str]) -> str:
    return f"""
      <a href="./ggShareLctreGroupWebView.do?lecture_no={item['id']}&amp;pageUnit=12&amp;key=10289&amp;pageIndex={item['page']}"
         class="receipt_anchor">
        <div class="receipt_row"><span class="temp_badge sm type2">{item['status']}</span></div>
        <p class="receipt_subject"><span>{html.escape(item['title'])}</span></p>
        <div class="receipt_target"><span>{item['method']}</span></div>
        <div class="receipt_content"><ul>
          <li><span class="lt">교육장소</span><span class="ld">{html.escape(item['venue'])}</span></li>
          <li><span class="lt">교육기간</span><span class="ld">{item['period']}</span></li>
          <li><span class="lt">이용요금</span><span class="ld">무료</span></li>
        </ul></div>
      </a>
    """


def _list_page(
    items: list[dict[str, str]],
    page: int,
    *,
    total: int,
    pages: int,
    include_totals: bool = True,
) -> str:
    total_markup = (
        f"총 <em class='em_black'>{total}</em>건 [<em>{page}</em>/{pages} 페이지]"
        if include_totals
        else ""
    )
    return f"<html><body>{total_markup}{''.join(_receipt(item) for item in items)}</body></html>"


def _local_detail(item: dict[str, str], *, share_id: str | None = None) -> str:
    share_id = share_id or item["id"]
    return f"""
      <html><body>
        <a class="btn" href="https://share.gg.go.kr/lecture/view?id={share_id}&amp;eshare=1">신청하기</a>
        <table>
          <caption>{html.escape(item['title'])} - 운영기관, 교육장소 정보제공</caption>
          <tr><th>구분</th><td>교육/강좌 (기타)</td></tr>
          <tr><th>교육장소</th><td>{html.escape(item['venue'])}</td></tr>
          <tr><th>교육시간</th><td>{item['period']}</td></tr>
          <tr><th>수강료</th><td>무료</td></tr>
        </table>
      </body></html>
    """


def _share_detail(item: dict[str, str]) -> str:
    branch = item["branch"]
    address = municipal.POCHEON_GGSHARE_BRANCHES[branch][1]
    return f"""
      <html><body><main>
        <p class="tit">{html.escape(item['title'])}</p>
        <span class="txt1">포천시</span><span class="txt2">{branch}</span>
        <ul><li>
          <span class="lineL">교육장소</span>
          <span class="txt has-btn-map">{address} {html.escape(item['venue'])}
            <button onclick="f_mapPop('{address}', '37.1', '127.1')">지도보기</button>
          </span>
        </li></ul>
      </main></body></html>
    """


def _live_shape_items() -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    current_positions = set(range(60)) | {96, 108, 109}
    items: list[dict[str, str]] = []
    by_id: dict[str, dict[str, str]] = {}
    for index in range(272):
        page = index // 12 + 1
        lecture_no = str(58000 - index)
        current = index in current_positions
        if index < 50:
            branch = "포천시청년센터"
            venue = "포천청년비전센터 3층" if index < 7 else "2층 청년센터"
            title = f"[포천음악창작소] 청년센터 운영 음악 과정 {index + 1}" if index < 7 else f"청년 과정 {index + 1}"
        elif index in set(range(50, 59)) | {96}:
            branch = "포천미디어센터"
            venue = "포천미디어센터 3층 '미디어교육실A'"
            title = f"미디어 과정 {index + 1}"
        elif index == 59:
            branch = "포천외국인주민지원센터"
            venue = "4층"
            title = "2026년 외국인주민 한국어교육"
        elif index in {108, 109}:
            branch = "포천음악창작소"
            venue = "포천시 청년비전센터 3층, 포천음악창작소"
            title = f"[포천음악창작소] 장기 관악 과정 {index}"
        else:
            branch = "포천시청년센터"
            venue = "2층 청년센터"
            title = f"지난 청년 과정 {index + 1}"

        if current:
            current_index = len([item for item in items if item["period"].startswith("2099")])
            status = "접수중" if current_index < 5 else "접수예정" if current_index < 28 else "접수마감"
            period = "2099-07-01 ~ 2099-12-31"
        else:
            status = "접수마감"
            period = "2000-01-01 ~ 2000-01-31"
        item = {
            "id": lecture_no,
            "page": str(page),
            "title": title,
            "status": status,
            "method": "인터넷 방문" if branch == "포천외국인주민지원센터" else "인터넷",
            "venue": venue,
            "period": period,
            "branch": branch,
        }
        items.append(item)
        by_id[lecture_no] = item
    return items, by_id


def test_pocheon_dispatch_completes_23_pages_and_collects_63_current_rows(monkeypatch) -> None:
    items, by_id = _live_shape_items()
    fetched: list[str] = []

    def fake_fetch_soup(_session: object, url: str, timeout: int = 20) -> BeautifulSoup:
        del timeout
        fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == municipal.POCHEON_GGSHARE_LIST_PATH:
            page = int((query.get("pageIndex") or ["1"])[0])
            start = (page - 1) * 12
            return _soup(_list_page(items[start:start + 12], page, total=272, pages=23))
        lecture_no = (query.get("lecture_no") or query.get("id") or [""])[0]
        item = by_id[lecture_no]
        if parsed.path == municipal.POCHEON_GGSHARE_DETAIL_PATH:
            return _soup(_local_detail(item))
        assert parsed.netloc == municipal.POCHEON_GGSHARE_SHARE_HOST
        assert parsed.path == municipal.POCHEON_GGSHARE_SHARE_PATH
        return _soup(_share_detail(item))

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch_soup)
    monkeypatch.setattr(municipal.time, "sleep", lambda _seconds: None)

    rows, parser, meta = municipal.collect_from_url(
        _target(), timeout=7, max_depth=0, max_pages=23, detail_limit=100
    )

    assert parser == municipal.POCHEON_GGSHARE_PARSER
    assert len(rows) == 63
    assert len({row["provider_course_id"] for row in rows}) == 63
    assert len({row["raw_url"] for row in rows}) == 63
    assert meta["pages"] == 23
    assert meta["total_count"] == 272
    assert meta["total_pages"] == 23
    assert meta["discovered_links"] == 272
    assert meta["current_count"] == 63
    assert meta["expired_count"] == 209
    assert meta["detail_candidates"] == 63
    assert meta["local_detail_pages"] == 63
    assert meta["share_detail_pages"] == 63
    assert meta["local_http_attempts"] == 63
    assert meta["share_http_attempts"] == 63
    assert meta["local_retry_recoveries"] == 0
    assert meta["share_retry_recoveries"] == 0
    assert meta["initial_local_detail_failure_ids"] == []
    assert meta["initial_share_detail_failure_ids"] == []
    assert meta["local_detail_failure_ids"] == []
    assert meta["share_detail_failure_ids"] == []
    assert meta["recovery_passes"] == 0
    assert meta["recovery_session_created"] is False
    assert meta["recovery_attempted_ids"] == []
    assert meta["reservation_discovery_links"] == 5
    assert meta["pagination_complete"] is True
    assert meta["pagination_exhausted"] is True
    assert meta["duplicate_count"] == 0
    assert "configured_collection_error" not in meta

    branch_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for row in rows:
        branch_counts[row["branch"]] = branch_counts.get(row["branch"], 0) + 1
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
        assert row["address"] == municipal.POCHEON_GGSHARE_BRANCHES[row["branch"]][1]
        assert row["preserve_branch"] is True
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["raw_fields"]["service_group_policy"] == "locked"
        lecture_no = row["raw_fields"]["lecture_no"]
        assert row["provider_course_id"] == f"{PROVIDER}:lecture:{lecture_no}"
        assert row["raw_url"] == municipal.pocheon_ggshare_local_detail_url(lecture_no)
        if row["status"] == "OPEN":
            assert row["application_url"] == municipal.pocheon_ggshare_share_url(lecture_no)
            assert row["application_type"] == "ONLINE_RESERVATION"
        else:
            assert "application_url" not in row
            assert row["application_type"] == "INFO_ONLY"

    assert branch_counts == {
        "포천시청년센터": 50,
        "포천미디어센터": 10,
        "포천음악창작소": 2,
        "포천외국인주민지원센터": 1,
    }
    assert status_counts == {"OPEN": 5, "SCHEDULED": 23, "CLOSED": 35}
    assert all(
        row["branch"] == "포천시청년센터"
        for row in rows
        if "청년센터 운영 음악 과정" in row["title"]
    )
    late_ids = {items[96]["id"], items[108]["id"], items[109]["id"]}
    assert late_ids <= {row["raw_fields"]["lecture_no"] for row in rows}
    assert len([url for url in fetched if urlparse(url).path == municipal.POCHEON_GGSHARE_LIST_PATH]) == 23
    assert len([url for url in fetched if urlparse(url).path == municipal.POCHEON_GGSHARE_DETAIL_PATH]) == 63
    assert len([url for url in fetched if urlparse(url).netloc == municipal.POCHEON_GGSHARE_SHARE_HOST]) == 63


def test_share_generic_200_is_retried_until_real_detail_dom(monkeypatch) -> None:
    item = {
        "id": "55557",
        "title": "[7월] 마음 이음 상담소 A",
        "venue": "2층 청년센터",
        "branch": "포천시청년센터",
    }
    generic = _soup(
        """
        <html><body><script>
          var template = '<p class="tit">[7월] 마음 이음 상담소 A</p>';
          var branch = '<span class="txt2">포천시청년센터</span>';
          var place = '<span class="lineL">교육장소</span>';
        </script></body></html>
        """
    )
    responses = [generic, _soup(_share_detail(item))]
    sleeps: list[float] = []
    calls: list[str] = []

    def fake_fetch(_session: object, url: str, timeout: int = 20) -> BeautifulSoup:
        del timeout
        calls.append(url)
        return responses.pop(0)

    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
    monkeypatch.setattr(municipal.time, "sleep", sleeps.append)
    fields, attempts = municipal.pocheon_ggshare_fetch_share_detail(
        object(),
        municipal.pocheon_ggshare_share_url(item["id"]),
        item["title"],
        timeout=5,
    )

    assert attempts == 2
    assert len(calls) == 2
    assert sleeps == [
        municipal.POCHEON_GGSHARE_DETAIL_PACING_SECONDS,
        municipal.POCHEON_GGSHARE_SHARE_BACKOFF_SECONDS,
        municipal.POCHEON_GGSHARE_DETAIL_PACING_SECONDS,
    ]
    assert fields["branch"] == "포천시청년센터"
    assert fields["address"] == "경기 포천시 호국로 1423"


def test_local_generic_200_is_retried_until_caption_and_matching_share_id(monkeypatch) -> None:
    item = {
        "id": "55557",
        "page": "1",
        "title": "[7월] 마음 이음 상담소 A",
        "status": "접수중",
        "method": "인터넷",
        "venue": "2층 청년센터",
        "period": "2099-01-01 ~ 2099-01-31",
        "branch": "포천시청년센터",
    }
    generic = _soup(
        """
        <html><body><script>
          var caption = '<caption>[7월] 마음 이음 상담소 A</caption>';
          var link = 'https://share.gg.go.kr/lecture/view?id=55557&eshare=1';
        </script></body></html>
        """
    )
    responses = [generic, _soup(_local_detail(item))]
    sleeps: list[float] = []
    calls: list[str] = []

    def fake_fetch(_session: object, url: str, timeout: int = 20) -> BeautifulSoup:
        del timeout
        calls.append(url)
        return responses.pop(0)

    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
    monkeypatch.setattr(municipal.time, "sleep", sleeps.append)
    fields, share_url, attempts = municipal.pocheon_ggshare_fetch_local_detail(
        object(),
        item["id"],
        item["title"],
        timeout=5,
    )

    assert attempts == 2
    assert len(calls) == 2
    assert sleeps == [
        municipal.POCHEON_GGSHARE_DETAIL_PACING_SECONDS,
        municipal.POCHEON_GGSHARE_LOCAL_BACKOFF_SECONDS,
        municipal.POCHEON_GGSHARE_DETAIL_PACING_SECONDS,
    ]
    assert fields["venue"] == "2층 청년센터"
    assert share_url == municipal.pocheon_ggshare_share_url(item["id"])


def test_duplicate_lecture_and_missing_later_page_totals_are_partial(monkeypatch) -> None:
    first = {
        "id": "70001",
        "page": "1",
        "title": "첫 강좌",
        "status": "접수마감",
        "method": "인터넷",
        "venue": "2층 청년센터",
        "period": "2000-01-01 ~ 2000-01-31",
        "branch": "포천시청년센터",
    }
    second = dict(first, id="70002", page="2", title="둘째 강좌")

    def collect_pages(page_html: dict[int, str]) -> dict[str, object]:
        def fake_fetch(_session: object, url: str, timeout: int = 20) -> BeautifulSoup:
            del timeout
            page = int((parse_qs(urlparse(url).query).get("pageIndex") or ["1"])[0])
            return _soup(page_html[page])

        monkeypatch.setattr(municipal, "session", lambda: object())
        monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
        return municipal.crawl_pocheon_ggshare_lectures(
            _target(), timeout=5, max_pages=2, detail_limit=0
        )[2]

    monkeypatch.setattr(municipal.time, "sleep", lambda _seconds: None)
    duplicate_meta = collect_pages(
        {
            1: _list_page([first], 1, total=2, pages=2),
            2: _list_page([dict(first, page="2")], 2, total=2, pages=2),
        }
    )
    assert duplicate_meta["duplicate_count"] == 1
    assert duplicate_meta["pagination_complete"] is False
    assert "duplicate lecture_no" in str(duplicate_meta["configured_collection_error"])

    missing_total_meta = collect_pages(
        {
            1: _list_page([first], 1, total=2, pages=2),
            2: _list_page([second], 2, total=2, pages=2, include_totals=False),
        }
    )
    assert missing_total_meta["pagination_exhausted"] is True
    assert missing_total_meta["pagination_complete"] is False
    assert "page 2 did not declare total rows and pages" in str(
        missing_total_meta["configured_collection_error"]
    )


def test_today_boundary_and_test_rows_are_filtered_before_detail(monkeypatch) -> None:
    today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    base = {
        "page": "1",
        "status": "접수마감",
        "method": "인터넷",
        "venue": "2층 청년센터",
        "branch": "포천시청년센터",
    }
    current = dict(base, id="71001", title="오늘 종료 강좌", period=f"{today} ~ {today}")
    expired = dict(base, id="71002", title="지난 강좌", period="2000-01-01 ~ 2000-01-31")
    test_row = dict(base, id="71003", title="테스트 강좌", period="2099-01-01 ~ 2099-01-31")
    by_id = {item["id"]: item for item in (current, expired, test_row)}
    detail_ids: list[str] = []

    def fake_fetch(_session: object, url: str, timeout: int = 20) -> BeautifulSoup:
        del timeout
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == municipal.POCHEON_GGSHARE_LIST_PATH:
            return _soup(_list_page([current, expired, test_row], 1, total=3, pages=1))
        lecture_no = (query.get("lecture_no") or query.get("id") or [""])[0]
        detail_ids.append(lecture_no)
        item = by_id[lecture_no]
        return _soup(_local_detail(item) if parsed.netloc != municipal.POCHEON_GGSHARE_SHARE_HOST else _share_detail(item))

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
    monkeypatch.setattr(municipal.time, "sleep", lambda _seconds: None)
    rows, _parser, meta = municipal.crawl_pocheon_ggshare_lectures(
        _target(), timeout=5, max_pages=1, detail_limit=3
    )

    assert [row["raw_fields"]["lecture_no"] for row in rows] == ["71001"]
    assert detail_ids == ["71001", "71001"]
    assert meta["expired_count"] == 1
    assert meta["test_rows"] == 1
    assert meta["pagination_complete"] is True


def test_detail_limit_cap_remains_a_core_collection_error(monkeypatch) -> None:
    branch = next(iter(municipal.POCHEON_GGSHARE_BRANCHES))
    item = {
        "id": "71996",
        "page": "1",
        "title": "Detail cap course",
        "status": "\uc811\uc218\uc911",
        "method": "\uc778\ud130\ub137",
        "venue": f"{branch} 2\uce35",
        "period": "2099-01-01 ~ 2099-01-31",
        "branch": branch,
    }
    detail_calls = 0

    def fake_fetch(_session: object, url: str, timeout: int = 20) -> BeautifulSoup:
        nonlocal detail_calls
        del timeout
        if urlparse(url).path == municipal.POCHEON_GGSHARE_LIST_PATH:
            return _soup(_list_page([item], 1, total=1, pages=1))
        detail_calls += 1
        raise AssertionError("detail fetch must remain capped")

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
    monkeypatch.setattr(municipal.time, "sleep", lambda _seconds: None)
    rows, _parser, meta = municipal.crawl_pocheon_ggshare_lectures(
        _target(), timeout=5, max_pages=1, detail_limit=0
    )

    assert rows == []
    assert detail_calls == 0
    assert meta["detail_enrichment_capped"] is True
    assert meta["detail_enrichment_complete"] is False
    assert meta["detail_failures"] == 0
    assert meta["detail_enrichment_warning"] == ""
    assert meta["list_pagination_complete"] is True
    assert meta["pagination_complete"] is False
    assert meta["list_collection_complete"] is False
    assert meta["core_fields_complete"] is False
    assert "detail enrichment capped" in meta["configured_collection_error"]


def test_primary_local_exhaustion_recovers_on_new_session_and_updates_exact_row(monkeypatch) -> None:
    branch = next(iter(municipal.POCHEON_GGSHARE_BRANCHES))
    item = {
        "id": "71997",
        "page": "1",
        "title": "Local recovery course",
        "status": "\uc811\uc218\uc911",
        "method": "\uc778\ud130\ub137",
        "venue": f"{branch} 2\uce35",
        "period": "2099-01-01 ~ 2099-01-31",
        "branch": branch,
    }
    recovered_item = dict(item, period="2099-02-01 ~ 2099-02-28")
    primary = _SessionMarker("primary")
    recovery = _SessionMarker("recovery")
    session_calls: list[_SessionMarker] = []
    local_sessions: list[str] = []
    share_sessions: list[str] = []
    sleeps: list[float] = []

    def fake_session() -> _SessionMarker:
        selected = (primary, recovery)[len(session_calls)]
        session_calls.append(selected)
        return selected

    def fake_fetch(active_session: _SessionMarker, url: str, timeout: int = 20) -> BeautifulSoup:
        del timeout
        parsed = urlparse(url)
        if parsed.path == municipal.POCHEON_GGSHARE_LIST_PATH:
            assert active_session is primary
            return _soup(_list_page([item], 1, total=1, pages=1))
        if parsed.path == municipal.POCHEON_GGSHARE_DETAIL_PATH:
            local_sessions.append(active_session.label)
            if active_session is primary:
                return _soup("<html><body>generic local HTTP 200 shell</body></html>")
            return _soup(_local_detail(recovered_item))
        share_sessions.append(active_session.label)
        assert active_session is recovery
        return _soup(_share_detail(recovered_item))

    monkeypatch.setattr(municipal, "session", fake_session)
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
    monkeypatch.setattr(municipal.time, "sleep", sleeps.append)
    rows, _parser, meta = municipal.crawl_pocheon_ggshare_lectures(
        _target(), timeout=5, max_pages=1, detail_limit=1
    )

    assert session_calls == [primary, recovery]
    assert recovery.closed is True
    assert local_sessions == ["primary"] * municipal.POCHEON_GGSHARE_LOCAL_DETAIL_ATTEMPTS + [
        "recovery"
    ]
    assert share_sessions == ["recovery"]
    assert municipal.POCHEON_GGSHARE_RECOVERY_COOLDOWN_SECONDS in sleeps
    assert len(rows) == 1
    row = rows[0]
    assert row["raw_fields"]["lecture_no"] == item["id"]
    assert row["period"] == recovered_item["period"]
    assert row["schedule_raw"] == recovered_item["period"]
    assert row["branch"] == branch
    assert row["application_url"] == municipal.pocheon_ggshare_share_url(item["id"])
    assert "local_detail_error" not in row["raw_fields"]
    assert row["raw_fields"]["local_detail_attempts"] == (
        municipal.POCHEON_GGSHARE_LOCAL_DETAIL_ATTEMPTS + 1
    )
    assert row["raw_fields"]["local_recovery_attempts"] == 1
    assert meta["initial_local_detail_failure_ids"] == [item["id"]]
    assert meta["local_detail_failure_ids"] == []
    assert meta["share_detail_failure_ids"] == []
    assert meta["recovery_complete_ids"] == [item["id"]]
    assert meta["local_http_attempts"] == municipal.POCHEON_GGSHARE_LOCAL_DETAIL_ATTEMPTS + 1
    assert meta["share_http_attempts"] == 1
    assert meta["local_recovery_recoveries"] == 1
    assert meta["share_recovery_recoveries"] == 1
    assert meta["pagination_complete"] is True
    assert "configured_collection_error" not in meta


def test_primary_share_exhaustion_recovers_on_new_session_without_refetching_local(monkeypatch) -> None:
    branch = next(iter(municipal.POCHEON_GGSHARE_BRANCHES))
    item = {
        "id": "71998",
        "page": "1",
        "title": "Share recovery course",
        "status": "\uc811\uc218\uc911",
        "method": "\uc778\ud130\ub137",
        "venue": f"{branch} 2\uce35",
        "period": "2099-01-01 ~ 2099-01-31",
        "branch": branch,
    }
    primary = _SessionMarker("primary")
    recovery = _SessionMarker("recovery")
    session_calls: list[_SessionMarker] = []
    local_sessions: list[str] = []
    share_sessions: list[str] = []

    def fake_session() -> _SessionMarker:
        selected = (primary, recovery)[len(session_calls)]
        session_calls.append(selected)
        return selected

    def fake_fetch(active_session: _SessionMarker, url: str, timeout: int = 20) -> BeautifulSoup:
        del timeout
        parsed = urlparse(url)
        if parsed.path == municipal.POCHEON_GGSHARE_LIST_PATH:
            assert active_session is primary
            return _soup(_list_page([item], 1, total=1, pages=1))
        if parsed.path == municipal.POCHEON_GGSHARE_DETAIL_PATH:
            local_sessions.append(active_session.label)
            assert active_session is primary
            return _soup(_local_detail(item))
        share_sessions.append(active_session.label)
        if active_session is primary:
            return _soup("<html><body>generic share HTTP 200 shell</body></html>")
        return _soup(_share_detail(item))

    monkeypatch.setattr(municipal, "session", fake_session)
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
    monkeypatch.setattr(municipal.time, "sleep", lambda _seconds: None)
    rows, _parser, meta = municipal.crawl_pocheon_ggshare_lectures(
        _target(), timeout=5, max_pages=1, detail_limit=1
    )

    assert session_calls == [primary, recovery]
    assert recovery.closed is True
    assert local_sessions == ["primary"]
    assert share_sessions == ["primary"] * municipal.POCHEON_GGSHARE_DETAIL_ATTEMPTS + [
        "recovery"
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["raw_fields"]["lecture_no"] == item["id"]
    assert row["branch"] == branch
    assert row["address"] == municipal.POCHEON_GGSHARE_BRANCHES[branch][1]
    assert "share_detail_error" not in row["raw_fields"]
    assert row["raw_fields"]["share_detail_attempts"] == (
        municipal.POCHEON_GGSHARE_DETAIL_ATTEMPTS + 1
    )
    assert meta["initial_share_detail_failure_ids"] == [item["id"]]
    assert meta["local_detail_failure_ids"] == []
    assert meta["share_detail_failure_ids"] == []
    assert meta["recovery_complete_ids"] == [item["id"]]
    assert meta["local_http_attempts"] == 1
    assert meta["share_http_attempts"] == municipal.POCHEON_GGSHARE_DETAIL_ATTEMPTS + 1
    assert meta["local_recovery_attempted"] == 0
    assert meta["share_recovery_recoveries"] == 1
    assert meta["pagination_complete"] is True
    assert "configured_collection_error" not in meta


def test_exhausted_local_recovery_is_warning_with_valid_fallback_and_no_application(monkeypatch) -> None:
    item = {
        "id": "71999",
        "page": "1",
        "title": "지역 상세 재시도 강좌",
        "status": "접수중",
        "method": "인터넷",
        "venue": "2층 청년센터",
        "period": "2099-01-01 ~ 2099-01-31",
        "branch": "포천시청년센터",
    }
    local_calls = 0
    share_calls = 0

    def fake_fetch(_session: object, url: str, timeout: int = 20) -> BeautifulSoup:
        nonlocal local_calls, share_calls
        del timeout
        parsed = urlparse(url)
        if parsed.path == municipal.POCHEON_GGSHARE_LIST_PATH:
            return _soup(_list_page([item], 1, total=1, pages=1))
        if parsed.path == municipal.POCHEON_GGSHARE_DETAIL_PATH:
            local_calls += 1
            return _soup("<html><body>generic local HTTP 200 shell</body></html>")
        share_calls += 1
        return _soup(_share_detail(item))

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
    monkeypatch.setattr(municipal.time, "sleep", lambda _seconds: None)
    rows, _parser, meta = municipal.crawl_pocheon_ggshare_lectures(
        _target(), timeout=5, max_pages=1, detail_limit=1
    )

    assert len(rows) == 1
    assert local_calls == (
        municipal.POCHEON_GGSHARE_LOCAL_DETAIL_ATTEMPTS
        + municipal.POCHEON_GGSHARE_RECOVERY_ATTEMPTS
    )
    assert share_calls == 0
    assert "application_url" not in rows[0]
    assert rows[0]["application_type"] == "INFO_ONLY"
    assert rows[0]["reservation_available"] is False
    assert rows[0]["branch"] == item["branch"]
    assert rows[0]["address"] == municipal.POCHEON_GGSHARE_BRANCHES[item["branch"]][1]
    assert rows[0]["period"] == item["period"]
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["raw_fields"]["clear_application_url"] is True
    assert rows[0]["raw_fields"]["detail_enrichment_complete"] is False
    assert rows[0]["raw_fields"]["detail_enrichment_warning"] == "local_detail_unavailable"
    assert "local_detail_error" in rows[0]["raw_fields"]
    assert meta["local_http_attempts"] == (
        municipal.POCHEON_GGSHARE_LOCAL_DETAIL_ATTEMPTS
        + municipal.POCHEON_GGSHARE_RECOVERY_ATTEMPTS
    )
    assert meta["local_detail_failures"] == 1
    assert meta["share_detail_failures"] == 0
    assert meta["initial_local_detail_failure_ids"] == [item["id"]]
    assert meta["local_detail_failure_ids"] == [item["id"]]
    assert meta["share_detail_failure_ids"] == []
    assert meta["recovery_attempted_ids"] == [item["id"]]
    assert meta["recovery_complete_ids"] == []
    assert meta["local_recovery_http_attempts"] == municipal.POCHEON_GGSHARE_RECOVERY_ATTEMPTS
    assert meta["recovery_passes"] == 1
    assert meta["pagination_exhausted"] is True
    assert meta["pagination_complete"] is True
    assert meta["list_collection_complete"] is True
    assert meta["core_fields_complete"] is True
    assert meta["detail_enrichment_complete"] is False
    assert "local detail unavailable" in meta["detail_enrichment_warning"]
    assert "configured_collection_error" not in meta
    assert meta["no_current_data"] is False


def test_exhausted_share_recovery_is_warning_and_keeps_exact_local_share_url(monkeypatch) -> None:
    item = {
        "id": "72001",
        "page": "1",
        "title": "재시도 강좌",
        "status": "접수중",
        "method": "인터넷",
        "venue": "2층 청년센터",
        "period": "2099-01-01 ~ 2099-01-31",
        "branch": "포천시청년센터",
    }
    share_calls = 0

    def fake_fetch(_session: object, url: str, timeout: int = 20) -> BeautifulSoup:
        nonlocal share_calls
        del timeout
        parsed = urlparse(url)
        if parsed.path == municipal.POCHEON_GGSHARE_LIST_PATH:
            return _soup(_list_page([item], 1, total=1, pages=1))
        if parsed.path == municipal.POCHEON_GGSHARE_DETAIL_PATH:
            return _soup(_local_detail(item))
        share_calls += 1
        return _soup("<html><body>generic HTTP 200 shell</body></html>")

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
    monkeypatch.setattr(municipal.time, "sleep", lambda _seconds: None)
    rows, _parser, meta = municipal.crawl_pocheon_ggshare_lectures(
        _target(), timeout=5, max_pages=1, detail_limit=1
    )

    assert len(rows) == 1
    assert share_calls == (
        municipal.POCHEON_GGSHARE_DETAIL_ATTEMPTS
        + municipal.POCHEON_GGSHARE_RECOVERY_ATTEMPTS
    )
    assert meta["share_http_attempts"] == (
        municipal.POCHEON_GGSHARE_DETAIL_ATTEMPTS
        + municipal.POCHEON_GGSHARE_RECOVERY_ATTEMPTS
    )
    assert meta["share_detail_failures"] == 1
    assert meta["initial_share_detail_failure_ids"] == [item["id"]]
    assert meta["local_detail_failure_ids"] == []
    assert meta["share_detail_failure_ids"] == [item["id"]]
    assert meta["recovery_attempted_ids"] == [item["id"]]
    assert meta["recovery_complete_ids"] == []
    assert meta["share_recovery_http_attempts"] == municipal.POCHEON_GGSHARE_RECOVERY_ATTEMPTS
    assert meta["recovery_passes"] == 1
    assert meta["pagination_exhausted"] is True
    assert rows[0]["application_url"] == municipal.pocheon_ggshare_share_url(item["id"])
    assert rows[0]["application_type"] == "ONLINE_RESERVATION"
    assert rows[0]["reservation_available"] is True
    assert "clear_application_url" not in rows[0]["raw_fields"]
    assert rows[0]["raw_fields"]["detail_enrichment_complete"] is False
    assert rows[0]["raw_fields"]["detail_enrichment_warning"] == "share_detail_unavailable"
    assert "share_detail_error" in rows[0]["raw_fields"]
    assert meta["pagination_complete"] is True
    assert meta["list_collection_complete"] is True
    assert meta["core_fields_complete"] is True
    assert meta["detail_enrichment_complete"] is False
    assert "share.gg detail unavailable" in meta["detail_enrichment_warning"]
    assert "configured_collection_error" not in meta
    assert meta["no_current_data"] is False


def test_exhausted_detail_with_unknown_fallback_remains_core_partial(monkeypatch) -> None:
    item = {
        "id": "72002",
        "page": "1",
        "title": "Unknown fallback course",
        "status": "\uc811\uc218\uc911",
        "method": "\uc778\ud130\ub137",
        "venue": "Unmapped lecture hall",
        "period": "2099-01-01 ~ 2099-01-31",
        "branch": "",
    }

    def fake_fetch(_session: object, url: str, timeout: int = 20) -> BeautifulSoup:
        del timeout
        parsed = urlparse(url)
        if parsed.path == municipal.POCHEON_GGSHARE_LIST_PATH:
            return _soup(_list_page([item], 1, total=1, pages=1))
        return _soup("<html><body>generic local HTTP 200 shell</body></html>")

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
    monkeypatch.setattr(municipal.time, "sleep", lambda _seconds: None)
    rows, _parser, meta = municipal.crawl_pocheon_ggshare_lectures(
        _target(), timeout=5, max_pages=1, detail_limit=1
    )

    assert rows == []
    assert meta["pagination_exhausted"] is True
    assert meta["list_pagination_complete"] is True
    assert meta["pagination_complete"] is False
    assert meta["list_collection_complete"] is False
    assert meta["core_fields_complete"] is False
    assert meta["core_validation_failure_ids"] == [item["id"]]
    assert meta["core_missing_fields_by_id"] == {item["id"]: ["official_branch"]}
    assert meta["detail_enrichment_complete"] is False
    assert "local detail unavailable" in meta["detail_enrichment_warning"]
    assert "official branch" in meta["configured_collection_error"]
    assert "core fields incomplete" in meta["configured_collection_error"]
    assert meta["no_current_data"] is False


def test_detail_enrichment_warning_allows_default_save_and_stale(monkeypatch) -> None:
    stale_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    save_calls: list[list[dict[str, object]]] = []

    class FakeConnection:
        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeWriter:
        def __init__(self, provider: str) -> None:
            assert provider == PROVIDER

        def save_rows(self, rows: list[dict[str, object]]) -> int:
            save_calls.append(rows)
            return len(rows)

    warning_row = {
        "provider": PROVIDER,
        "provider_course_id": f"{PROVIDER}:lecture:72001",
        "title": "Detail warning course",
        "branch": next(iter(municipal.POCHEON_GGSHARE_BRANCHES)),
        "raw_url": municipal.pocheon_ggshare_local_detail_url("72001"),
    }
    monkeypatch.setattr(municipal, "load_targets", lambda *_args, **_kwargs: [_target()])
    monkeypatch.setattr(
        municipal,
        "collect_from_url",
        lambda *_args, **_kwargs: (
            [warning_row],
            municipal.POCHEON_GGSHARE_PARSER,
            {
                "pages": 23,
                "pagination_complete": True,
                "list_collection_complete": True,
                "core_fields_complete": True,
                "detail_enrichment_complete": False,
                "detail_enrichment_warning": "local detail unavailable for 1 current rows",
            },
        ),
    )
    monkeypatch.setattr(municipal, "get_db_connection", FakeConnection)
    monkeypatch.setattr(municipal, "MunicipalDbWriter", FakeWriter)
    monkeypatch.setattr(
        municipal,
        "mark_stale_courses",
        lambda *args, **kwargs: stale_calls.append((args, kwargs)) or 0,
    )

    reports = municipal.run(
        source="municipal",
        target_limit=None,
        per_target_limit=0,
        min_score=0,
        include_review=True,
        save_db=True,
        mark_stale=True,
        max_depth=0,
        max_pages=30,
        detail_limit=100,
        timeout=5,
    )

    assert reports[0].success is True
    assert reports[0].saved == 1
    assert reports[0].configured_collection_error == ""
    assert save_calls == [[warning_row]]
    assert len(stale_calls) == 1
    assert stale_calls[0][0][0] == PROVIDER
    assert stale_calls[0][1]["source_endpoint"] == municipal.canonical_source_endpoint(
        TARGET_URL
    )


def test_core_partial_default_blocks_save_and_stale_but_bounded_opt_in_can_save(
    monkeypatch,
) -> None:
    stale_calls: list[tuple[object, ...]] = []
    save_calls: list[list[dict[str, object]]] = []

    class FakeConnection:
        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeWriter:
        def __init__(self, provider: str) -> None:
            assert provider == PROVIDER

        def save_rows(self, rows: list[dict[str, object]]) -> int:
            save_calls.append(rows)
            return len(rows)

    partial_row = {
        "provider": PROVIDER,
        "provider_course_id": f"{PROVIDER}:lecture:72001",
        "title": "부분 수집 강좌",
        "branch": "포천시청년센터",
        "raw_url": municipal.pocheon_ggshare_local_detail_url("72001"),
    }
    monkeypatch.setattr(municipal, "load_targets", lambda *_args, **_kwargs: [_target()])
    monkeypatch.setattr(
        municipal,
        "collect_from_url",
        lambda *_args, **_kwargs: (
            [partial_row],
            municipal.POCHEON_GGSHARE_PARSER,
            {
                "pages": 1,
                "pagination_complete": False,
                "configured_collection_error": "max_pages cap reached after 1 of 23 declared pages",
            },
        ),
    )
    monkeypatch.setattr(municipal, "get_db_connection", FakeConnection)
    monkeypatch.setattr(municipal, "MunicipalDbWriter", FakeWriter)
    monkeypatch.setattr(
        municipal,
        "mark_stale_courses",
        lambda *args: stale_calls.append(args) or 0,
    )

    reports = municipal.run(
        source="municipal",
        target_limit=None,
        per_target_limit=0,
        min_score=0,
        include_review=True,
        save_db=True,
        mark_stale=True,
        max_depth=0,
        max_pages=1,
        detail_limit=1,
        timeout=5,
    )

    assert reports[0].success is True
    assert reports[0].saved == 0
    assert reports[0].configured_collection_error.startswith("max_pages cap reached")
    assert save_calls == []
    assert stale_calls == []

    opt_in_reports = municipal.run(
        source="municipal",
        target_limit=None,
        per_target_limit=1,
        min_score=0,
        include_review=True,
        save_db=True,
        mark_stale=True,
        max_depth=0,
        max_pages=1,
        detail_limit=1,
        timeout=5,
        allow_partial_save=True,
    )

    assert opt_in_reports[0].success is True
    assert opt_in_reports[0].saved == 1
    assert len(save_calls) == 1
    assert stale_calls == []


def test_writer_clears_application_url_only_for_explicit_nested_json_boolean_true(
    monkeypatch,
) -> None:
    branch = next(iter(municipal.POCHEON_GGSHARE_BRANCHES))
    branch_code, branch_address = municipal.POCHEON_GGSHARE_BRANCHES[branch]
    writer = municipal.MunicipalDbWriter(PROVIDER)
    missing = object()

    def make_course(lecture_no: str, signal: object = missing) -> dict[str, object]:
        crawler_raw_fields: dict[str, object] = {"lecture_no": lecture_no}
        if signal is not missing:
            crawler_raw_fields["clear_application_url"] = signal
        row = {
            "provider": PROVIDER,
            "provider_course_id": f"{PROVIDER}:lecture:{lecture_no}",
            "title": f"Writer clear signal {lecture_no}",
            "branch": branch,
            "branch_code": branch_code,
            "venue_name": branch,
            "venue_address": branch_address,
            "address": branch_address,
            "room": f"{branch} 2\uce35",
            "period": "2099-01-01 ~ 2099-01-31",
            "status": "OPEN",
            "raw_url": municipal.pocheon_ggshare_local_detail_url(lecture_no),
            "application_type": "INFO_ONLY",
            "reservation_available": False,
            "collection_category": "\uacf5\uacf5\uc608\uc57d",
            "domain_category": "\uad50\uc721\u00b7\uac15\uc88c",
            "source_group": "municipal_reservation",
            "operator_type": "\uc9c0\uc790\uccb4/\uacf5\uacf5\uae30\uad00",
            "service_group": "\uacf5\uacf5\uac15\uc88c",
            "service_group_policy": "locked",
            "raw_fields": crawler_raw_fields,
        }
        return writer.normalize_course(row, "branch-id")

    bool_course = make_course("73001", True)
    missing_course = make_course("73002")
    string_course = make_course("73003", "true")
    old_url = "https://share.gg.go.kr/lecture/view?id=old&eshare=1"
    stored_urls = {
        bool_course["provider_course_id"]: old_url,
        missing_course["provider_course_id"]: old_url,
        string_course["provider_course_id"]: old_url,
    }
    executed: list[tuple[str, dict[str, object]]] = []

    class FakeCursor:
        def execute(self, query: str, params: dict[str, object]) -> None:
            executed.append((query, params))
            raw_payload = params["raw_fields"].adapted
            provider_course_id = params["provider_course_id"]
            if raw_payload.get("clear_application_url") is True:
                stored_urls[provider_course_id] = None
            elif params.get("application_url") is not None:
                stored_urls[provider_course_id] = params["application_url"]

    cursor = FakeCursor()

    class CursorContext:
        def __enter__(self) -> FakeCursor:
            return cursor

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(municipal, "get_db_cursor", CursorContext)
    monkeypatch.setattr(
        municipal,
        "coalesce_provider_course_id_by_raw_url",
        lambda *_args, **_kwargs: None,
    )

    assert writer.save_course(bool_course) is True
    assert writer.save_course(missing_course) is True
    assert writer.save_course(string_course) is True

    assert stored_urls[bool_course["provider_course_id"]] is None
    assert stored_urls[missing_course["provider_course_id"]] == old_url
    assert stored_urls[string_course["provider_course_id"]] == old_url
    assert bool_course["raw_fields"].adapted["clear_application_url"] is True
    assert "clear_application_url" not in missing_course["raw_fields"].adapted
    assert "clear_application_url" not in string_course["raw_fields"].adapted
    assert len(executed) == 3
    assert all(
        "EXCLUDED.raw_fields -> 'clear_application_url' = 'true'::jsonb" in query
        and "THEN NULL" in query
        and "ELSE COALESCE(EXCLUDED.application_url, courses.application_url)" in query
        for query, _params in executed
    )


def test_pocheon_route_requires_exact_official_provider_and_url() -> None:
    assert municipal.is_pocheon_ggshare_target(TARGET_URL) is True
    assert municipal.is_pocheon_ggshare_target(TARGET_URL.replace("https://", "http://")) is False
    assert municipal.is_pocheon_ggshare_target(TARGET_URL.replace("key=10289", "key=1")) is False
    assert municipal.pocheon_ggshare_local_detail_url("58013") == (
        "https://pocheon.go.kr/yeyak/ggShareLctreGroupWebView.do?lecture_no=58013&key=10289"
    )
