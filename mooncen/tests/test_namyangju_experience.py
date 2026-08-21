from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from Crawler import municipal_namyangju_experience as experience


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


def _branch_nav() -> str:
    return "".join(
        '<a href="/reserve/selectUserExprnTourBasicInfoList.do?'
        f'key={item.menu_key}&amp;searchExprnKey={item.code}">'
        f"{escape(item.name)}</a>"
        for item in experience.NAMYANGJU_EXPERIENCE_BRANCHES
    )


def _source_rows() -> list[dict[str, str | int]]:
    branches = experience.NAMYANGJU_EXPERIENCE_BRANCHES
    rows: list[dict[str, str | int]] = []
    for index in range(12):
        branch = branches[index % len(branches)]
        identity = 200 - index
        rows.append(
            {
                "number": 12 - index,
                "identity": str(identity),
                "branch_code": branch.code,
                "branch": branch.name,
                "category": "체험",
                "title": f"공식 체험 {identity}",
                "apply_period": "2026-08-01 ~ 2026-08-31",
                "method": "온라인",
                "status": "접수중" if index < 2 else "접수마감",
            }
        )
    return rows


def _list_html(page: int, *, empty_clamp: bool = False) -> str:
    rows = _source_rows()
    rendered_page = min(page, 2)
    selected = rows[:10] if rendered_page == 1 else rows[10:]
    if empty_clamp and page > 2:
        selected = []
    body = []
    for row in selected:
        href = (
            "./selectUserExprnTourBasicInfoView.do?key=3383&amp;"
            f"searchTourKey={row['identity']}&amp;searchExprnKey={row['branch_code']}"
        )
        values = (
            str(row["number"]),
            str(row["branch"]),
            str(row["category"]),
            f'<a href="{href}">{escape(str(row["title"]))}</a>',
            str(row["apply_period"]),
            str(row["method"]),
            str(row["status"]),
        )
        body.append("<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>")
    headers = "".join(f"<th>{value}</th>" for value in experience._LIST_HEADERS)
    return f"""
    <html><head><title>전체보기 - 체험·견학 프로그램 - 체험·견학 - 통합예약포털</title></head>
    <body>
      {_branch_nav()}
      <form id="exprnTourBasicInfoSearchVO" method="GET" action="./selectUserExprnTourBasicInfoList.do">
        <input name="key" value="3383">
      </form>
      <div class="bbs_page"><span class="item count">총 12건</span>
        <span class="item page">[<em>{rendered_page}</em>/2페이지]</span></div>
      <table><thead><tr>{headers}</tr></thead><tbody>{''.join(body)}</tbody></table>
    </body></html>
    """


def _detail_html(row: dict[str, str | int], *, wrong_title: bool = False) -> str:
    title = "다른 제목" if wrong_title else str(row["title"])
    return f"""
    <html><head><title>전체보기 - 체험·견학 프로그램 - 체험·견학 - 통합예약포털</title></head>
    <body><div class="p_experience_wrap">
      <h3><em class="p_status">{row['status']}</em>{escape(title)}</h3>
      <div class="p_exp_tit_wrap">
        <div class="titbox_wrap"><span class="tit">기관</span><span class="con">{row['branch']}</span></div>
        <div class="titbox_wrap"><span class="tit">카테고리</span><span class="con">{row['category']}</span></div>
        <div class="titbox_wrap"><span class="tit">접수방법</span><span class="con">{row['method']}</span></div>
        <div class="titbox_wrap"><span class="tit">접수기간</span><span class="con">{row['apply_period']}</span></div>
        <div class="titbox_wrap"><span class="tit">운영기간</span><span class="con">2026-09-01 ~ 2026-09-30</span></div>
      </div>
      <div class="p_exp_cont_wrap">
        <table class="table type2"><tbody>
          <tr><th>소요시간</th><td>60분</td></tr>
          <tr><th>장소</th><td>{row['branch']}</td></tr>
          <tr><th>모집대상</th><td>누구나</td></tr>
          <tr><th>신청인원</th><td>1명 ~ 20명 가능</td></tr>
          <tr><th>이용요금</th><td>무료</td></tr>
          <tr><th>재료비/체험비</th><td>무료</td></tr>
        </tbody></table>
        <a id="redirectRegisBtn" href="#n">날짜선택</a>
      </div>
    </div></body></html>
    """


def _fetcher(*, wrong_detail: bool = False, empty_clamp: bool = False):
    rows = {str(row["identity"]): row for row in _source_rows()}
    requested: list[str] = []

    def fetch(_session: Session, url: str, _timeout: int) -> Response:
        requested.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == experience.NAMYANGJU_EXPERIENCE_LIST_PATH:
            page = int(query.get("pageIndex", ["1"])[0])
            body = _list_html(page, empty_clamp=empty_clamp)
        elif parsed.path == experience.NAMYANGJU_EXPERIENCE_DETAIL_PATH:
            identity = query["searchTourKey"][0]
            body = _detail_html(
                rows[identity], wrong_title=wrong_detail and identity == "200"
            )
        else:
            raise AssertionError(f"unsafe request: {url}")
        return Response(url, body.encode("utf-8"))

    return fetch, requested


def _target(url: str = experience.NAMYANGJU_EXPERIENCE_URL) -> dict[str, str]:
    return {"provider": experience.NAMYANGJU_EXPERIENCE_PROVIDER, "url": url}


def test_exact_target_and_url_builders_reject_unsafe_variants() -> None:
    assert experience.is_namyangju_experience_target(_target())
    assert not experience.is_namyangju_experience_target(
        _target(experience.NAMYANGJU_EXPERIENCE_URL + "&pageIndex=1")
    )
    assert experience.namyangju_experience_list_url(2).endswith(
        "key=3383&pageIndex=2"
    )
    assert experience.namyangju_experience_detail_url(17, 4).endswith(
        "key=3383&searchTourKey=17&searchExprnKey=4"
    )


def test_collects_complete_open_snapshot_with_clamp_and_public_details() -> None:
    fetcher, requested = _fetcher()
    rows, parser, meta = experience.collect_namyangju_experience_courses(
        _target(),
        today="2026-08-05",
        max_pages=3,
        detail_limit=2,
        session_factory=Session,
        fetcher=fetcher,
    )

    assert parser == experience.NAMYANGJU_EXPERIENCE_PARSER
    assert len(rows) == 2
    assert {row["source_course_id"] for row in rows} == {
        "experience:3:200",
        "experience:10:199",
    }
    assert all(row["domain_category"] == "체험·견학" for row in rows)
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["classification_locked"] is True for row in rows)
    assert all(row["application_url"] == "" for row in rows)
    assert meta["source_total"] == 12
    assert meta["source_pages"] == 2
    assert meta["clamp_page"] == 3
    assert meta["current_source_count"] == 2
    assert meta["detail_pages"] == 2
    assert meta["snapshot_complete"] is True
    assert meta["application_endpoint_requests"] == 0
    assert len(requested) == 8
    assert all("selectUserExpTourDetail.do" not in url for url in requested)
    assert all("login" not in url.lower() for url in requested)


def test_detail_limit_and_detail_drift_fail_before_any_partial_publish() -> None:
    fetcher, requested = _fetcher()
    rows, _, meta = experience.collect_namyangju_experience_courses(
        _target(),
        max_pages=3,
        detail_limit=1,
        session_factory=Session,
        fetcher=fetcher,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["detail_requests"] == 0
    assert all("selectUserExprnTourBasicInfoView.do" not in url for url in requested)

    fetcher, _ = _fetcher(wrong_detail=True)
    rows, _, meta = experience.collect_namyangju_experience_courses(
        _target(),
        max_pages=3,
        detail_limit=2,
        session_factory=Session,
        fetcher=fetcher,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "title/status drift" in meta["errors"][0]


def test_changed_post_last_clamp_fails_closed() -> None:
    fetcher, _ = _fetcher(empty_clamp=True)
    rows, _, meta = experience.collect_namyangju_experience_courses(
        _target(),
        max_pages=3,
        detail_limit=2,
        session_factory=Session,
        fetcher=fetcher,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "row count changed" in meta["errors"][0]


def test_router_and_operational_configs_are_wired(monkeypatch) -> None:
    from Crawler import Crawler_MunicipalYaml as municipal

    sentinel = ([{"id": 1}], experience.NAMYANGJU_EXPERIENCE_PARSER, {"snapshot_complete": True})
    captured: dict[str, object] = {}

    def collect(*_args, **kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        experience, "collect_namyangju_experience_courses", collect
    )
    target = municipal.CrawlTarget(
        provider=experience.NAMYANGJU_EXPERIENCE_PROVIDER,
        name="남양주시 체험·견학",
        branch="경기도 남양주시",
        url=experience.NAMYANGJU_EXPERIENCE_URL,
        source="test",
    )
    assert municipal.collect_from_url(
        target, timeout=3, max_pages=20, detail_limit=100
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
        if item.get("provider") == experience.NAMYANGJU_EXPERIENCE_PROVIDER
        and item.get("url") == experience.NAMYANGJU_EXPERIENCE_URL
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
        if item.get("provider") == experience.NAMYANGJU_EXPERIENCE_PROVIDER
        and item.get("target_url") == experience.NAMYANGJU_EXPERIENCE_URL
    ]
    assert len(entries) == 1
    assert entries[0]["validation_outcome"] == "collected"
    assert entries[0]["row_count"] == 15
