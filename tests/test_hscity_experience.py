from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from Crawler import municipal_hscity_experience as experience


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


def _source_rows(
    source: experience.HwaseongExperienceSource,
) -> list[dict[str, str]]:
    if source.key == "visit":
        return [
            {
                "identity": str(800 - index),
                "title": f"공식 견학 {800 - index}",
                "institution": "만세구 체험기관",
                "place": "화성시 체험장",
                "status": "신청하기" if index == 0 else "신청마감",
                "district": "만세구",
            }
            for index in range(11)
        ]
    return [
        {
            "identity": "900",
            "title": "공식 체험 예정",
            "institution": "동탄구 체험기관",
            "place": "화성시 체험장",
            "status": "접수예정",
            "district": "동탄구",
        },
        {
            "identity": "899",
            "title": "공식 체험 마감",
            "institution": "동탄구 체험기관",
            "place": "화성시 체험장",
            "status": "신청마감",
            "district": "동탄구",
        },
    ]


def _registry() -> str:
    return "".join(
        '<li class="header-gnb-depth3-item">'
        f'<a href="{source.list_path}">{escape(source.label)}</a></li>'
        for source in experience.HSCITY_EXPERIENCE_SOURCES
    )


def _card(
    source: experience.HwaseongExperienceSource,
    row: dict[str, str],
) -> str:
    open_status = row["status"] == "신청하기"
    classes = "sub-card-btn orange half-margin" if open_status else "sub-card-btn none half-margin"
    onclick = (
        f' onclick="javascript:fnApply(\'{row["identity"]}\'); return false;"'
        if open_status
        else ""
    )
    return f"""
    <div class="sub-card-item">
      <p class="sub-card-info-title">
        <a onclick="fnDetail('{row['identity']}');">{escape(row['title'])}</a>
      </p>
      <ul class="sub-card-info-list">
        <li><dl><dt>기관</dt><dd>{escape(row['institution'])}</dd></dl></li>
        <li><dl><dt>장소</dt><dd>{escape(row['place'])}</dd></dl></li>
        <li><dl><dt>{source.list_fee_label}</dt><dd>무료</dd></dl></li>
      </ul>
      <div class="sub-card-btn-box">
        <button class="{classes}"{onclick}>{row['status']}</button>
      </div>
    </div>
    """


def _list_html(
    source: experience.HwaseongExperienceSource,
    page: int,
    *,
    nonempty_sentinel: bool = False,
) -> str:
    rows = _source_rows(source)
    last = max(1, (len(rows) + 9) // 10)
    selected = rows[(page - 1) * 10 : page * 10] if page <= last else []
    if nonempty_sentinel and page == last + 1:
        selected = rows[:1]
    cards = "".join(_card(source, row) for row in selected)
    pager = ""
    if selected:
        pager = f"""
        <div class="table-pagination">
          <button onclick="fnList({last});"></button>
          <ul class="page-list"><li class="active">{page}</li></ul>
        </div>
        """
    return f"""
    <html><head><title>화성특례시 통합예약시스템</title></head><body>
      <ul>{_registry()}</ul>
      <form name="paramForm" method="get">
        <input name="currentPageNo" value="{page}">
        <input name="recordCountPerPage" value="10">
      </form>
      <div class="sub-card-list">{cards}</div>
      {pager}
    </body></html>
    """


def _detail_html(
    source: experience.HwaseongExperienceSource,
    row: dict[str, str],
    *,
    wrong_title: bool = False,
    missing_district: bool = False,
) -> str:
    title = "다른 프로그램" if wrong_title else row["title"]
    active = row["status"] == "신청하기"
    button_class = "sub-small-btn active" if active else "sub-small-btn"
    onclick = ' onclick="fnApply(); return false;"' if active else ""
    district = "" if missing_district else row["district"]
    return f"""
    <html><head><title>화성특례시 통합예약시스템</title></head><body>
      <form name="paramForm" method="get">
        <input name="{source.identity_param}" value="{row['identity']}">
      </form>
      <div class="detail-info">
        <p class="detail-info-head-title">{escape(title)}</p>
        <ul class="detail-info-list">
          <li><dl><dt>운영기관</dt><dd>{escape(row['institution'])}</dd></dl></li>
          <li><dl><dt>장소</dt><dd>{escape(row['place'])}</dd></dl></li>
          <li><dl><dt>주요대상</dt><dd>누구나</dd></dl></li>
          <li><dl><dt>이용료</dt><dd>무료</dd></dl></li>
          <li><dl><dt>신청기간</dt><dd>2026-08-01 ~ 2026-08-31</dd></dl></li>
          <li><dl><dt>이용기간</dt><dd>2026-09-01 ~ 2026-09-30</dd></dl></li>
          <li><dl><dt>정원수</dt><dd>20명</dd></dl></li>
        </ul>
        <div class="detail-info-btn">
          <button class="{button_class}"{onclick}>{row['status']}</button>
        </div>
      </div>
      <script>
        var map = "kakao.com/link/map/경기도 화성시 {district} 테스트로 1,37.1,127.1";
      </script>
    </body></html>
    """


def _fetcher(
    *,
    wrong_detail: bool = False,
    nonempty_sentinel: bool = False,
    missing_district: bool = False,
):
    by_list = {
        source.list_path: source
        for source in experience.HSCITY_EXPERIENCE_SOURCES
    }
    by_detail = {
        source.detail_path: source
        for source in experience.HSCITY_EXPERIENCE_SOURCES
    }
    rows = {
        (source.key, row["identity"]): row
        for source in experience.HSCITY_EXPERIENCE_SOURCES
        for row in _source_rows(source)
    }
    requested: list[str] = []

    def fetch(_session: Session, url: str, _timeout: int) -> Response:
        requested.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path in by_list:
            source = by_list[parsed.path]
            page = int(query["currentPageNo"][0])
            body = _list_html(
                source,
                page,
                nonempty_sentinel=nonempty_sentinel,
            )
        elif parsed.path in by_detail:
            source = by_detail[parsed.path]
            identity = query[source.identity_param][0]
            body = _detail_html(
                source,
                rows[(source.key, identity)],
                wrong_title=wrong_detail and identity == "800",
                missing_district=missing_district and identity == "800",
            )
        else:
            raise AssertionError(f"unsafe request: {url}")
        return Response(url, body.encode("utf-8"))

    return fetch, requested


def _target(url: str = experience.HSCITY_EXPERIENCE_URL) -> dict[str, str]:
    return {"provider": experience.HSCITY_EXPERIENCE_PROVIDER, "url": url}


def test_exact_target_and_public_url_builders_are_locked() -> None:
    assert experience.is_hscity_experience_target(_target())
    assert not experience.is_hscity_experience_target(
        _target(experience.HSCITY_EXPERIENCE_URL + "?currentPageNo=1")
    )
    visit, exprn = experience.HSCITY_EXPERIENCE_SOURCES
    assert experience.hscity_experience_list_url(visit, 2).endswith(
        "/visitList.do?currentPageNo=2&recordCountPerPage=10"
    )
    assert experience.hscity_experience_detail_url(exprn, 7).endswith(
        "/exprnDetail.do?exprnIdx=7"
    )


def test_collects_two_complete_ledgers_and_current_public_details() -> None:
    from Crawler.Crawler_MunicipalYaml import MunicipalDbWriter

    fetcher, requested = _fetcher()
    rows, parser, meta = experience.collect_hscity_experience_courses(
        _target(),
        today="2026-08-05",
        max_pages=2,
        detail_limit=2,
        session_factory=Session,
        fetcher=fetcher,
    )

    assert parser == experience.HSCITY_EXPERIENCE_PARSER
    assert len(rows) == 2
    assert {row["status"] for row in rows} == {"OPEN", "SCHEDULED"}
    assert {row["municipality_code"] for row in rows} == {
        "4159100000",
        "4159700000",
    }
    assert all(row["domain_category"] == "체험·견학" for row in rows)
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["application_url"] == "" for row in rows)
    assert all(row["branch_lat"] is not None for row in rows)
    assert all(row["branch_lon"] is not None for row in rows)
    saved_branches = [
        MunicipalDbWriter(experience.HSCITY_EXPERIENCE_PROVIDER).branch_info_from_row(row)
        for row in rows
    ]
    assert all(branch["lat"] is not None for branch in saved_branches)
    assert all(branch["lon"] is not None for branch in saved_branches)
    assert all(branch["coordinate_source"] == "official_course_detail" for branch in saved_branches)
    assert all(branch["location_confidence"] == 100 for branch in saved_branches)
    assert all(branch["location_verified"] is True for branch in saved_branches)
    assert meta["source_total"] == 13
    assert meta["source_pages"] == 3
    assert meta["current_source_count"] == 2
    assert meta["detail_pages"] == 2
    assert meta["snapshot_complete"] is True
    assert meta["application_endpoint_requests"] == 0
    assert len(requested) == 13
    assert all("Apply.do" not in url and "login" not in url.lower() for url in requested)


def test_detail_limit_and_detail_contract_fail_atomically() -> None:
    fetcher, requested = _fetcher()
    rows, _, meta = experience.collect_hscity_experience_courses(
        _target(),
        max_pages=2,
        detail_limit=1,
        session_factory=Session,
        fetcher=fetcher,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["detail_requests"] == 0
    assert all("Detail.do" not in url for url in requested)

    fetcher, _ = _fetcher(wrong_detail=True)
    rows, _, meta = experience.collect_hscity_experience_courses(
        _target(),
        max_pages=2,
        detail_limit=2,
        session_factory=Session,
        fetcher=fetcher,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "detail title mismatch" in meta["errors"][0]


def test_nonempty_sentinel_and_missing_official_district_fail_closed() -> None:
    fetcher, _ = _fetcher(nonempty_sentinel=True)
    rows, _, meta = experience.collect_hscity_experience_courses(
        _target(),
        max_pages=2,
        detail_limit=2,
        session_factory=Session,
        fetcher=fetcher,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "last page" in meta["errors"][0]

    fetcher, _ = _fetcher(missing_district=True)
    rows, _, meta = experience.collect_hscity_experience_courses(
        _target(),
        max_pages=2,
        detail_limit=2,
        session_factory=Session,
        fetcher=fetcher,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "official 2026 district address changed" in meta["errors"][0]


def test_exact_dispatch_and_operational_configs_are_wired(monkeypatch) -> None:
    from Crawler import Crawler_MunicipalYaml as municipal

    sentinel = (
        [{"id": 1}],
        experience.HSCITY_EXPERIENCE_PARSER,
        {"snapshot_complete": True},
    )
    captured: dict[str, object] = {}

    def collect(*_args, **kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        experience, "collect_hscity_experience_courses", collect
    )
    target = municipal.CrawlTarget(
        provider=experience.HSCITY_EXPERIENCE_PROVIDER,
        name="화성특례시 체험·견학",
        branch="경기도 화성시",
        url=experience.HSCITY_EXPERIENCE_URL,
        source="test",
    )
    assert municipal.collect_from_url(
        target, timeout=3, max_pages=30, detail_limit=200
    ) == sentinel
    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])

    targets = yaml.safe_load(
        (ROOT / "config/crawl_targets/public_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )["targets"]
    matches = [
        row
        for row in targets
        if row.get("provider") == experience.HSCITY_EXPERIENCE_PROVIDER
        and row.get("url") == experience.HSCITY_EXPERIENCE_URL
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
        row
        for row in operational
        if row.get("provider") == experience.HSCITY_EXPERIENCE_PROVIDER
        and row.get("target_url") == experience.HSCITY_EXPERIENCE_URL
    ]
    assert len(entries) == 1
    assert entries[0]["validation_outcome"] == "collected"
    assert entries[0]["row_count"] == 63
