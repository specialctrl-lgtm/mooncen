from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from Crawler import municipal_guri_experience as experience


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Response:
    url: str
    content: bytes
    status_code: int = 200

    def __post_init__(self) -> None:
        self.history: list[object] = []
        self.headers = {"Content-Type": "text/html; charset=utf-8"}


class Session:
    def close(self) -> None:
        pass


def _rows(source: experience.GuriExperienceSource) -> list[dict[str, str]]:
    if source.code != "insect_ecology":
        return []
    return [
        {
            "identity": str(800 - index),
            "title": f"공식 체험 {800 - index}",
            "status": "접수마감" if index == 0 else "접수중",
        }
        for index in range(11)
    ]


def _registry() -> str:
    links = "".join(
        '<li><a href="/reserve/selectWebEdcList.do?'
        f'key={source.menu_key}&amp;searchAuthSite={source.auth_site}">'
        f"{escape(source.branch)}</a></li>"
        for source in experience.GURI_EXPERIENCE_SOURCES
    )
    return f'<ul class="tab_list">{links}</ul>'


def _card(
    source: experience.GuriExperienceSource,
    row: dict[str, str],
) -> str:
    detail = experience.guri_experience_detail_url(source, row["identity"])
    href = "#" if row["status"] == "접수마감" else detail
    return f"""
    <div class="facility_item">
      <div class="facility_title"><span class="category">{row['status']}</span>
        <a class="tit" href="{escape(href)}">{escape(row['title'])}</a></div>
      <input name="edcNo" value="{row['identity']}">
      <div class="temp_contactbox"><ul>
        <li><em class="title">프로그램 구분</em><span class="text">체험</span></li>
        <li><em class="title">대상</em><span class="text">누구나</span></li>
        <li><em class="title">정원수</em><span class="text">20</span></li>
        <li><em class="title">신청기간</em><span class="text">2026-08-01 ~ 2026-08-31</span></li>
        <li><em class="title">프로그램 기간</em><span class="text">2026-09-01 ~ 2026-09-30</span></li>
        <li><em class="title">문의</em><span class="text">031-000-0000</span></li>
      </ul></div>
    </div>
    """


def _list_html(
    source: experience.GuriExperienceSource,
    page: int,
    *,
    nonempty_sentinel: bool = False,
) -> str:
    rows = _rows(source)
    total = len(rows)
    last = max(1, (total + 9) // 10)
    if page <= last:
        selected = rows[(page - 1) * 10 : page * 10]
    else:
        selected = rows[:1] if nonempty_sentinel and rows else []
    cards = "".join(_card(source, row) for row in selected)
    return f"""
    <html><head><title>{source.branch} - 구리시 통합예약포털</title></head><body>
      {_registry()}
      <h2>{source.branch}</h2>
      <form name="bbsNttSearchForm" method="get" action="./selectWebEdcList.do">
        <input name="key" value="{source.menu_key}">
      </form>
      <div class="bbs_page">총게시물 : {total} 건 페이지 : {page} /{last}
        <em>*접수중인 프로그램만 표시됩니다.</em></div>
      <div class="facility_box_list">{cards}</div>
    </body></html>
    """


def _detail_html(
    source: experience.GuriExperienceSource,
    row: dict[str, str],
    *,
    wrong_title: bool = False,
) -> str:
    title = "다른 체험" if wrong_title else row["title"]
    return f"""
    <html><head><title>{source.branch} - 구리시 통합예약포털</title></head><body>
      <h2>{source.branch}</h2>
      <div class="schedule_box"><div class="s_title">{escape(title)}</div></div>
    </body></html>
    """


def _fetcher(*, wrong_detail: bool = False, nonempty_sentinel: bool = False):
    by_key = {
        (source.menu_key, source.auth_site): source
        for source in experience.GURI_EXPERIENCE_SOURCES
    }
    rows = {
        (source.auth_site, row["identity"]): row
        for source in experience.GURI_EXPERIENCE_SOURCES
        for row in _rows(source)
    }
    requested: list[str] = []

    def fetch(_session: Session, url: str, _timeout: int) -> Response:
        requested.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        source = by_key[(query["key"][0], query["searchAuthSite"][0])]
        if parsed.path == experience.GURI_EXPERIENCE_LIST_PATH:
            page = int(query.get("pageIndex", ["1"])[0])
            body = _list_html(
                source, page, nonempty_sentinel=nonempty_sentinel
            )
        elif parsed.path == experience.GURI_EXPERIENCE_DETAIL_PATH:
            identity = query["edcNo"][0]
            body = _detail_html(
                source,
                rows[(source.auth_site, identity)],
                wrong_title=wrong_detail and identity == "800",
            )
        else:
            raise AssertionError(f"unsafe request: {url}")
        return Response(url, body.encode("utf-8"))

    return fetch, requested


def _target(url: str = experience.GURI_EXPERIENCE_URL) -> dict[str, str]:
    return {"provider": experience.GURI_EXPERIENCE_PROVIDER, "url": url}


def _two_sources(monkeypatch) -> None:
    monkeypatch.setattr(
        experience,
        "GURI_EXPERIENCE_SOURCES",
        experience.GURI_EXPERIENCE_SOURCES[:2],
    )


def test_registry_and_exact_target_are_locked() -> None:
    assert len(experience.GURI_EXPERIENCE_SOURCES) == 11
    assert experience.is_guri_experience_target(_target())
    assert not experience.is_guri_experience_target(
        _target(experience.GURI_EXPERIENCE_URL + "&pageIndex=1")
    )
    source = experience.GURI_EXPERIENCE_SOURCES[0]
    assert experience.guri_experience_list_url(source, 2).endswith(
        "key=3888&searchAuthSite=AUTE01&pageIndex=2"
    )
    assert experience.guri_experience_detail_url(source, 7).endswith(
        "key=3888&edcNo=7&searchAuthSite=AUTE01"
    )


def test_collects_all_current_future_rows_and_zero_source(monkeypatch) -> None:
    _two_sources(monkeypatch)
    fetcher, requested = _fetcher()
    rows, parser, meta = experience.collect_guri_experience_courses(
        _target(),
        today="2026-08-05",
        max_pages=3,
        detail_limit=11,
        session_factory=Session,
        fetcher=fetcher,
    )

    assert parser == experience.GURI_EXPERIENCE_PARSER
    assert len(rows) == 11
    assert len({row["provider_course_id"] for row in rows}) == 11
    assert all(row["domain_category"] == "체험·견학" for row in rows)
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["application_url"] == "" for row in rows)
    assert meta["source_count"] == 2
    assert meta["source_total"] == 11
    assert meta["source_pages"] == 3
    assert meta["current_source_count"] == 11
    assert meta["detail_pages"] == 11
    assert meta["branch_counts"] == {
        "곤충생태관": 11,
        "장자호수생태체험관": 0,
    }
    assert meta["zero_branch_count"] == 1
    assert meta["snapshot_complete"] is True
    assert meta["reservation_endpoint_requests"] == 0
    assert len(requested) == 22
    assert all("Regist" not in url and "login" not in url.lower() for url in requested)


def test_limit_and_calendar_title_drift_fail_atomically(monkeypatch) -> None:
    _two_sources(monkeypatch)
    fetcher, requested = _fetcher()
    rows, _, meta = experience.collect_guri_experience_courses(
        _target(),
        max_pages=3,
        detail_limit=10,
        session_factory=Session,
        fetcher=fetcher,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["detail_requests"] == 0
    assert all("selectWebEdcCalendar.do" not in url for url in requested)

    fetcher, _ = _fetcher(wrong_detail=True)
    rows, _, meta = experience.collect_guri_experience_courses(
        _target(),
        max_pages=3,
        detail_limit=11,
        session_factory=Session,
        fetcher=fetcher,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "calendar title binding changed" in meta["errors"][0]


def test_nonempty_post_last_sentinel_fails_closed(monkeypatch) -> None:
    _two_sources(monkeypatch)
    fetcher, _ = _fetcher(nonempty_sentinel=True)
    rows, _, meta = experience.collect_guri_experience_courses(
        _target(),
        max_pages=3,
        detail_limit=11,
        session_factory=Session,
        fetcher=fetcher,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "row count changed" in meta["errors"][0]


def test_router_and_operational_configs_are_wired(monkeypatch) -> None:
    from Crawler import Crawler_MunicipalYaml as municipal

    sentinel = ([{"id": 1}], experience.GURI_EXPERIENCE_PARSER, {"snapshot_complete": True})
    captured: dict[str, object] = {}

    def collect(*_args, **kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(experience, "collect_guri_experience_courses", collect)
    target = municipal.CrawlTarget(
        provider=experience.GURI_EXPERIENCE_PROVIDER,
        name="구리시 체험·견학",
        branch="경기도 구리시",
        url=experience.GURI_EXPERIENCE_URL,
        source="test",
    )
    assert municipal.collect_from_url(
        target, timeout=3, max_pages=60, detail_limit=400
    ) == sentinel
    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])

    targets = yaml.safe_load(
        (ROOT / "config/crawl_targets/public_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )["targets"]
    matches = [
        item
        for item in targets
        if item.get("provider") == experience.GURI_EXPERIENCE_PROVIDER
        and item.get("url") == experience.GURI_EXPERIENCE_URL
    ]
    assert len(matches) == 1
    assert matches[0]["crawler_status"] == "ready"
    assert matches[0]["service_group"] == "체험"
    assert matches[0]["last_quality"]["snapshot_complete"] is True

    operational = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )["entries"]
    entries = [
        item
        for item in operational
        if item.get("provider") == experience.GURI_EXPERIENCE_PROVIDER
        and item.get("target_url") == experience.GURI_EXPERIENCE_URL
    ]
    assert len(entries) == 1
    assert entries[0]["validation_outcome"] == "collected"
    assert entries[0]["row_count"] == 37
