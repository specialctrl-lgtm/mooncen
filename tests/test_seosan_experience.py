from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import yaml

from Crawler import Crawler_MunicipalYaml as router
from Crawler import municipal_seosan_experience as seosan


ROOT = Path(__file__).resolve().parents[1]


ROWS = {
    "city_library": [
        {
            "id": "101",
            "title": "찾아가는 1일 어린이 독서교실",
            "apply": "2019-08-16~2019-09-06",
            "event": "2019-09-18~2019-11-28",
            "status": "접수마감",
            "facility": "시립도서관",
        }
    ],
    "children_library": [
        {
            "id": "261",
            "title": "미디어창작공간 스튜디온(편집실 PC-A)",
            "apply": "2026-04-01~2026-12-31",
            "event": "2026-04-01~2026-12-31",
            "status": "접수중",
            "facility": "스튜디온(편집실 PC-A)",
        },
        {
            "id": "241",
            "title": "미디어창작공간 스튜디온(스튜디오 A)",
            "apply": "2026-04-01~2026-12-31",
            "event": "2026-04-01~2026-12-31",
            "status": "접수중",
            "facility": "스튜디온(스튜디오 A)",
        },
        {
            "id": "166",
            "title": "찾아가는 도서관(9시 이전 신청건은 자동 취소)",
            "apply": "2026-07-14~2026-07-14",
            "event": "2026-09-03~2026-12-10",
            "status": "접수마감",
            "facility": "어린이도서관[찾아가는 도서관]",
        },
        {
            "id": "165",
            "title": "Book적book적 도서관체험(9시 이전 신청건은 자동 취소)",
            "apply": "2026-07-14~2026-07-14",
            "event": "2026-09-02~2026-12-09",
            "status": "접수마감",
            "facility": "어린이도서관[Book적book적 도서관체험]",
        },
        {
            "id": "163",
            "title": "도움 터 도서관(9시 이전 신청건은 자동 취소)",
            "apply": "2026-07-14~2026-07-14",
            "event": "2026-08-28~2026-12-11",
            "status": "접수마감",
            "facility": "어린이도서관[도움 터 도서관]",
        },
    ],
    "city_safety_center": [
        {
            "id": "81",
            "title": "도시안전통합센터 견학",
            "apply": "2019-01-01~2019-12-31",
            "event": "2019-01-01~2019-12-31",
            "status": "접수마감",
            "facility": "도시안전통합센터",
        }
    ],
}


@dataclass
class Response:
    url: str
    html: str
    status_code: int = 200

    def __post_init__(self) -> None:
        self.content = self.html.encode("utf-8")
        self.history: list[object] = []
        self.headers = {"Content-Type": "text/html; charset=utf-8"}


class Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _directory_html(*, drift: bool = False) -> str:
    entries = []
    for partition in seosan.SEOSAN_EXPERIENCE_PARTITIONS:
        if drift and partition.key == "city_safety_center":
            continue
        entries.append(
            f'<li><a href="{escape(urlparse(_base_url(partition)).path + "?" + urlparse(_base_url(partition)).query)}">'
            f"{escape(partition.label)}</a></li>"
        )
    return f"""
      <html><head><title>통합예약시스템</title></head><body>
        <nav id="lnb"><ul class="top_menu">
          <li class="depth1">
            <a class="depth1_ti" href="/total/selectFcltyResveSrvcListU.do?key=14&amp;searchFcltyNo=121">
              체험 / 견학
            </a>
            <ul class="depth2">{''.join(entries)}</ul>
          </li>
        </ul></nav>
      </body></html>
    """


def _base_url(partition: seosan.SeosanExperiencePartition) -> str:
    return (
        f"https://{seosan.SEOSAN_EXPERIENCE_HOST}{seosan.SEOSAN_EXPERIENCE_LIST_PATH}?"
        f"{urlencode(partition.query)}"
    )


def _search_form(partition: seosan.SeosanExperiencePartition) -> str:
    values = dict(partition.query)
    instt = values.get("searchInstt", "")
    facility = values.get("searchFcltyNo", "")
    return f"""
      <form id="fcltyResveSrvcForm" method="get" action="./selectFcltyResveSrvcListU.do">
        <input name="key" value="{values['key']}">
        <input name="searchResveSrvcSe" value="fclty">
        <select name="searchInstt"><option value="">전체</option>
          <option value="05"{' selected' if instt == '05' else ''}>어린이도서관</option>
        </select>
        <select name="searchFcltyNo"><option value="">전체</option>
          <option value="121"{' selected' if facility == '121' else ''}>시립도서관</option>
          <option value="101"{' selected' if facility == '101' else ''}>도시안전통합센터</option>
        </select>
        <input name="searchRceptBgnde" value="">
        <input name="searchRceptEndde" value="">
        <input name="searchCnd" value="restSrvcNm">
        <input name="searchKrwd" value="">
      </form>
    """


def _identity_href(
    partition: seosan.SeosanExperiencePartition, identity: str, page: int
) -> str:
    values = dict(partition.query)
    query = [
        ("key", values["key"]),
        ("fcltyResveSrvcNo", identity),
        ("searchResveSrvcSe", "fclty"),
        ("searchInstt", values.get("searchInstt", "")),
        ("searchFcltyNo", values.get("searchFcltyNo", "")),
        ("searchRceptBgnde", ""),
        ("searchRceptEndde", ""),
        ("pageUnit", str(seosan.SEOSAN_EXPERIENCE_PAGE_SIZE)),
        ("searchCnd", "all"),
        ("searchKrwd", ""),
        ("pageIndex", str(page)),
    ]
    return f"./fcltyResveSrvcViewU.do?{urlencode(query)}"


def _list_row(
    partition: seosan.SeosanExperiencePartition, row: dict[str, str], page: int
) -> str:
    return f"""
      <tr>
        <td>1</td>
        <td><a href="{escape(_identity_href(partition, row['id'], page))}">{escape(row['title'])}</a></td>
        <td>접수 : {row['apply']} 사용 : {row['event']}</td>
        <td>041-660-0200</td><td>선착순</td><td>{row['status']}</td>
      </tr>
    """


def _list_html(
    partition: seosan.SeosanExperiencePartition,
    page: int,
    *,
    sentinel_nonempty: bool = False,
    replacement_title: str = "",
) -> str:
    rows = [dict(row) for row in ROWS[partition.key]]
    if replacement_title and partition.key == "children_library":
        rows[0]["title"] = replacement_title
    selected = rows if page == 1 else []
    if sentinel_nonempty and page == 2:
        selected = rows[:1]
    row_html = "".join(_list_row(partition, row, page) for row in selected)
    if not selected:
        row_html = '<tr><td colspan="6">등록된 게시물이 없습니다.</td></tr>'
    return f"""
      <html><head><title>{partition.label} - 통합예약시스템</title></head><body>
        {_search_form(partition)}
        <div>총 게시물 {len(rows)} 개 , 페이지 {page} / 1</div>
        <table class="bbs_default list">
          <thead><tr><th>번호</th><th>예약서비스명</th><th>접수/사용기간</th>
            <th>문의전화</th><th>예약방법</th><th>접수상태</th></tr></thead>
          <tbody>{row_html}</tbody>
        </table>
      </body></html>
    """


def _detail_html(
    partition: seosan.SeosanExperiencePartition,
    row: dict[str, str],
    *,
    wrong_title: bool = False,
) -> str:
    title = "다른 체험" if wrong_title else row["facility"]
    key = dict(partition.query)["key"]
    identity = row["id"]
    controls = "".join(
        f'<a href="?key={key}&amp;fcltyResveSrvcNo={identity}&amp;searchY=2026&amp;searchM={month}">{label}</a>'
        for month, label in (("07", "이전달"), ("09", "다음달"))
    )
    return f"""
      <html><head><title>{partition.label} - 통합예약시스템</title></head><body>
        <article id="contents">
          <div class="detail_tit_top"><strong>{escape(title)}</strong>
            <span>{row['status']}</span><span>선착순</span></div>
          <div class="day_plan"><div class="bg_in"><ul class="total_bu">
            <li><span>시설명</span>{escape(row['facility'])}</li>
            <li><span>기관정보</span>{escape(partition.institution)}</li>
            <li><span>예약접수기간</span>{row['apply']}</li>
            <li><span>이용기간</span>{row['event']}</li>
            <li><span>예약방법</span>온라인 예약 / 선착순</li>
            <li><span>문의전화</span>041-660-0200</li>
          </ul></div></div>
          {controls}<a href="#n">예약마감</a>
        </article>
      </body></html>
    """


class FixtureSource:
    def __init__(
        self,
        *,
        sentinel_nonempty: bool = False,
        directory_drift: bool = False,
        replacement_title: str = "",
        wrong_detail_title: bool = False,
    ) -> None:
        self.sentinel_nonempty = sentinel_nonempty
        self.directory_drift = directory_drift
        self.replacement_title = replacement_title
        self.wrong_detail_title = wrong_detail_title
        self.urls: list[str] = []
        self.directory_calls = 0

    def __call__(self, _session: Session, url: str, _timeout: int) -> Response:
        self.urls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == seosan.SEOSAN_EXPERIENCE_DIRECTORY_PATH:
            self.directory_calls += 1
            return Response(
                url,
                _directory_html(
                    drift=self.directory_drift and self.directory_calls > 1
                ),
            )
        partition = next(
            partition
            for partition in seosan.SEOSAN_EXPERIENCE_PARTITIONS
            if query.get("key") == [dict(partition.query)["key"]]
        )
        if parsed.path == seosan.SEOSAN_EXPERIENCE_LIST_PATH:
            page = int(query["pageIndex"][0])
            return Response(
                url,
                _list_html(
                    partition,
                    page,
                    sentinel_nonempty=self.sentinel_nonempty,
                    replacement_title=self.replacement_title,
                ),
            )
        if parsed.path == seosan.SEOSAN_EXPERIENCE_DETAIL_PATH:
            identity = query["fcltyResveSrvcNo"][0]
            row = next(row for row in ROWS[partition.key] if row["id"] == identity)
            return Response(
                url,
                _detail_html(
                    partition,
                    row,
                    wrong_title=self.wrong_detail_title and identity == "165",
                ),
            )
        raise AssertionError(f"unexpected request: {url}")


def _target() -> dict[str, str]:
    return {
        "provider": seosan.SEOSAN_EXPERIENCE_PROVIDER,
        "url": seosan.SEOSAN_EXPERIENCE_URL,
    }


def _collect(source: FixtureSource, **kwargs):
    return seosan.collect_seosan_experience(
        _target(),
        today="2026-08-05",
        timeout=10,
        max_pages=5,
        detail_limit=10,
        session_factory=Session,
        fetcher=source,
        **kwargs,
    )


def test_complete_three_partition_snapshot_excludes_two_studio_rentals() -> None:
    source = FixtureSource()
    rows, parser, meta = _collect(source)

    assert parser == seosan.SEOSAN_EXPERIENCE_PARSER
    assert [row["source_course_id"] for row in rows] == [
        "experience:children_library:163",
        "experience:children_library:165",
        "experience:children_library:166",
    ]
    assert all(row["service_group"] == "체험" for row in rows)
    assert all(row["classification_locked"] is True for row in rows)
    assert all(row["application_url"] == "" for row in rows)
    assert all("041-" not in repr(row) for row in rows)
    assert meta["source_total"] == 7
    assert meta["experience_source_count"] == 5
    assert meta["contextual_excluded_count"] == 2
    assert meta["contextual_exclusion_reasons"] == {"facility_or_studio_rental": 2}
    assert meta["expired_count"] == 2
    assert meta["current_source_count"] == meta["detail_verified"] == meta["returned_count"] == 3
    assert meta["directory_requests"] == 2
    assert meta["list_requests"] == 12
    assert meta["detail_requests"] == 3
    assert meta["logical_requests"] == 17
    assert meta["snapshot_complete"] is meta["full_snapshot_validated"] is True
    assert all(meta["boundary_rechecks"][key] == {"first": True, "last": True, "sentinel": True} for key in ROWS)

    requested_paths = {urlparse(url).path for url in source.urls}
    assert requested_paths == {
        seosan.SEOSAN_EXPERIENCE_DIRECTORY_PATH,
        seosan.SEOSAN_EXPERIENCE_LIST_PATH,
        seosan.SEOSAN_EXPERIENCE_DETAIL_PATH,
    }
    assert not any(token in url.lower() for url in source.urls for token in ("login", "add", "apply", "applicant", "download"))


def test_notice_like_reservation_row_is_contextually_excluded() -> None:
    rows, _parser, meta = _collect(FixtureSource(replacement_title="어린이도서관 운영 안내"))
    assert len(rows) == 3
    assert meta["contextual_exclusion_reasons"] == {
        "facility_or_studio_rental": 1,
        "notice_or_editorial": 1,
    }


def test_unknown_mixed_menu_row_fails_closed() -> None:
    rows, _parser, meta = _collect(FixtureSource(replacement_title="미디어창작공간 이용"))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "unclassified experience-menu row" in meta["errors"][0]


def test_nonempty_immediate_sentinel_fails_atomically() -> None:
    rows, _parser, meta = _collect(FixtureSource(sentinel_nonempty=True))
    assert rows == []
    assert meta["returned_count"] == 0
    assert meta["pagination_complete"] is False
    assert "post-last sentinel" in meta["errors"][0]


def test_directory_or_detail_identity_drift_fails_atomically() -> None:
    for source in (FixtureSource(directory_drift=True), FixtureSource(wrong_detail_title=True)):
        rows, _parser, meta = _collect(source)
        assert rows == []
        assert meta["snapshot_complete"] is False
        assert meta["returned_count"] == 0


def test_exact_target_and_safe_url_builders() -> None:
    assert seosan.is_seosan_experience_target(_target())
    assert not seosan.is_seosan_experience_target(
        {**_target(), "url": seosan.SEOSAN_EXPERIENCE_URL + "&pageIndex=1"}
    )
    assert not seosan.is_seosan_experience_target(
        {**_target(), "provider": "MUNI_TOTAL_SEOSAN_GO_KR_F5ACE4CA"}
    )
    partition = seosan.SEOSAN_EXPERIENCE_PARTITIONS[1]
    assert parse_qs(urlparse(seosan.seosan_experience_list_url(partition, 2)).query) == {
        "key": ["646"],
        "searchInstt": ["05"],
        "pageUnit": ["50"],
        "pageIndex": ["2"],
    }
    assert parse_qs(urlparse(seosan.seosan_experience_detail_url(partition, "165")).query) == {
        "key": ["646"],
        "fcltyResveSrvcNo": ["165"],
        "searchResveSrvcSe": ["fclty"],
        "searchInstt": ["05"],
    }


def test_router_dispatches_exact_experience_sibling(monkeypatch) -> None:
    expected = ([{"ok": True}], "seosan-experience", {"snapshot_complete": True})
    calls: list[object] = []

    def fake_collect(target, **kwargs):
        calls.append((target, kwargs))
        return expected

    monkeypatch.setattr(seosan, "collect_seosan_experience", fake_collect)
    target = router.CrawlTarget(
        provider=seosan.SEOSAN_EXPERIENCE_PROVIDER,
        name="서산시 체험·견학",
        branch="서산시 통합예약",
        url=seosan.SEOSAN_EXPERIENCE_URL,
        source="test",
    )
    assert router.collect_from_url(target, max_pages=5, detail_limit=10) == expected
    assert len(calls) == 1


def test_target_operational_and_coverage_linkage() -> None:
    public = yaml.safe_load(
        (ROOT / "config/crawl_targets/public_reservation.yaml").read_text(encoding="utf-8")
    )
    targets = [
        target
        for target in public["targets"]
        if target.get("provider") == seosan.SEOSAN_EXPERIENCE_PROVIDER
        and target.get("url") == seosan.SEOSAN_EXPERIENCE_URL
    ]
    assert len(targets) == 1
    assert targets[0]["crawler_module"] == "Crawler.municipal_seosan_experience"
    assert targets[0]["service_group"] == "체험"
    assert targets[0]["full_snapshot_required"] is True

    operational = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert any(
        entry.get("provider") == seosan.SEOSAN_EXPERIENCE_PROVIDER
        and entry.get("target_url") == seosan.SEOSAN_EXPERIENCE_URL
        and entry.get("row_count") == 3
        for entry in operational["entries"]
    )

    coverage = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_coverage.yaml").read_text(
            encoding="utf-8"
        )
    )
    municipality = next(
        item for item in coverage["municipalities"] if item.get("code") == "4421000000"
    )
    assert any(
        evidence.get("provider") == seosan.SEOSAN_EXPERIENCE_PROVIDER
        and evidence.get("target_url") == seosan.SEOSAN_EXPERIENCE_URL
        for evidence in municipality["evidence"]
    )
