from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
import yaml

from Crawler import municipal_sejong_library as library


ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, url: str, html: str) -> None:
        self.url = url
        self.status_code = 200
        self.headers = {"Content-Type": "text/html; charset=UTF-8"}
        self.content = html.encode("utf-8")


def _dl(label: str, value: str) -> str:
    return f"<dl><dt>{label}</dt><dd>{value}</dd></dl>"


def _info(label: str, value: str) -> str:
    return f'<dl class="info"><dt>{label}</dt><dd>{value}</dd></dl>'


def _card(
    edu_idx: str,
    title: str,
    operation: str,
    application: str,
    target: str,
    capacity: str,
    *,
    opened: bool,
) -> str:
    state = (
        f'<a class="btn_sm btn_ing" href="regist.do?edu_idx={edu_idx}&amp;prepage=x">수강신청</a>'
        if opened
        else '<a class="btn_sm btn_close" href="#javascript:;">기간종료</a>'
    )
    target_dl = _dl("수강대상", target) if target else ""
    return f"""
    <li><div class="cont">
      <p class="cate">시립도서관</p>
      <p class="tit"><a href="view.do?edu_idx={edu_idx}&amp;prepage=x">[문화행사] {title}</a></p>
      <div class="sm_box">
        {_dl('신청기간', application)}
        {_dl('운영기간', operation)}
        {target_dl}
        {_dl('모집인원', capacity)}
      </div>
    </div><div class="btn_box">{state}
      <a class="btn_sm btn_check" href="user.do?edu_idx={edu_idx}&amp;prepage=x">등록확인</a>
    </div></li>
    """


ROWS = {
    "101": {
        "title": "현재 신청 강좌",
        "operation": "2026-08-07 ~ 2026-09-04 09:30~11:30 (금요일)",
        "application": "2026-07-29 10:00 ~ 2026-08-20 18:00",
        "target": "세종시민",
        "capacity": "2 / 15 명 (대기:5명)",
        "opened": True,
        "room": "문화교실2",
    },
    # The real archive contains both an impossible application date and a
    # reversed historical operation year.  Neither can make an expired row
    # current, and neither may poison every future snapshot.
    "100": {
        "title": "과거 공식 오류 강좌",
        "operation": "2025-01-17 ~ 2024-03-28 10:00~12:00 (금요일)",
        "application": "2024-06-19 10:00 ~ 2024-06-31 18:00",
        "target": "",
        "capacity": "25 / 20 명 (대기:5명)",
        "opened": False,
        "room": "문화교실1",
    },
    "99": {
        "title": "진행 중 마감 강좌",
        "operation": "2026-05-15 ~ 2026-08-21 19:00~21:00 (금요일)",
        "application": "2026-04-27 10:00 ~ 2026-05-11 18:00",
        "target": "청소년",
        "capacity": "15 / 15 명 (선착순)",
        "opened": False,
        "room": "세종시립도서관 3층 이도",
    },
}


def _list_html(ids: list[str], *, changed_title: bool = False) -> str:
    cards = []
    for edu_idx in ids:
        row = ROWS[edu_idx]
        title = f"{row['title']} 변경" if changed_title else row["title"]
        cards.append(
            _card(
                edu_idx,
                title,
                row["operation"],
                row["application"],
                row["target"],
                row["capacity"],
                opened=row["opened"],
            )
        )
    return f"""
    <html><head><title>독서문화 프로그램 신청 | 세종시립도서관</title></head><body>
      <form id="frm_edu" method="get" action="list.do">
        <input name="sh_ct_idx"><input name="sh_ct_idx2" value="54">
        <select name="v_status"></select><select name="v_search"></select>
        <input name="v_keyword">
      </form>
      <div class="board_total_left">총 <strong>3</strong>건</div>
      <div id="board"><div class="lesson"><ul>{''.join(cards)}</ul></div></div>
    </body></html>
    """


def _detail_html(edu_idx: str, *, changed_title: bool = False) -> str:
    row = ROWS[edu_idx]
    title = f"{row['title']} 변경" if changed_title else row["title"]
    start_end, schedule = row["operation"].rsplit(" ", 2)[0], " ".join(row["operation"].rsplit(" ", 2)[1:])
    # The current fixtures all have YYYY-MM-DD ~ YYYY-MM-DD followed by time/day.
    start_end = " ~ ".join(
        [part.strip().split()[0] for part in row["operation"].split("~", 1)]
    )
    schedule = row["operation"].split("~", 1)[1].strip().split(" ", 1)[1]
    if row["opened"]:
        state = '<a class="btn_sm btn_receipt" href="#javascript:;">신청중</a>'
        apply = f'<a class="con_btn btn_receipt" href="regist.do?edu_idx={edu_idx}&amp;prepage=x">신청</a>'
    else:
        state = '<a class="btn_sm btn_close" href="#javascript:;">기간종료</a>'
        apply = ""
    fields = "".join(
        [
            _info("강좌기간", start_end),
            _info("강좌시간", schedule),
            _info("신청기간", row["application"]),
            _info("신청방법", "인터넷접수"),
            _info("수강대상", row["target"]),
            _info("모집인원", row["capacity"]),
            _info("강의실", row["room"]),
            _info("참가비", "무료"),
            _info("강사", "저장하지 않을 이름"),
            _info("첨부파일", '<a href="down.do?edu_idx=101">계획서.pdf</a>'),
        ]
    )
    return f"""
    <html><head><title>독서문화 프로그램 신청 | 세종시립도서관</title></head><body>
      <div id="board"><div class="table_bview"><table>
        <thead><tr><th>{title} {state}</th></tr></thead>
        <tbody><tr><td>{fields}<div class="content"><img src="/private-image.png"></div></td></tr></tbody>
      </table></div><div class="btn_w">{apply}<a class="con_btn gray" href="list.do">목록</a></div></div>
    </body></html>
    """


class FakeSite:
    def __init__(
        self,
        *,
        clamp_changed: bool = False,
        stable_first_changed: bool = False,
        detail_changed: str = "",
    ) -> None:
        self.clamp_changed = clamp_changed
        self.stable_first_changed = stable_first_changed
        self.detail_changed = detail_changed
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.page_counts: Counter[int] = Counter()

    def session_factory(self) -> "FakeSite":
        return self

    def close(self) -> None:
        return None

    def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        timeout: int,
        allow_redirects: bool,
    ) -> FakeResponse:
        assert timeout > 0
        assert allow_redirects is False
        parsed = urlparse(url)
        if parsed.path == library.SEJONG_LIBRARY_LIST_PATH:
            assert params is not None
            expected_keys = {
                "v_page",
                "sh_ct_idx",
                "sh_ct_idx2",
                "v_status",
                "v_search",
                "v_keyword",
            }
            assert set(params) == expected_keys
            assert params["sh_ct_idx"] == ""
            assert params["sh_ct_idx2"] == "54"
            assert params["v_status"] == params["v_search"] == params["v_keyword"] == ""
            page = int(params["v_page"])
            self.page_counts[page] += 1
            ids = ["101", "100"] if page == 1 else ["99"]
            changed = (page == 3 and self.clamp_changed) or (
                page == 1 and self.page_counts[page] > 1 and self.stable_first_changed
            )
            final_url = f"{url}?{urlencode(params)}"
            self.calls.append(("list", final_url, dict(params)))
            return FakeResponse(final_url, _list_html(ids, changed_title=changed))
        if parsed.path == library.SEJONG_LIBRARY_DETAIL_PATH:
            assert params is None
            edu_idx = (parse_qs(parsed.query).get("edu_idx") or [""])[0]
            assert edu_idx in {"101", "99"}
            self.calls.append(("detail", url, {}))
            return FakeResponse(
                url,
                _detail_html(edu_idx, changed_title=edu_idx == self.detail_changed),
            )
        raise AssertionError(f"forbidden endpoint was requested: {url}")


@pytest.fixture(autouse=True)
def small_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, "SEJONG_LIBRARY_PAGE_SIZE", 2)


def _target(**updates: Any) -> dict[str, Any]:
    target = {
        "provider": library.SEJONG_LIBRARY_PROVIDER,
        "url": library.SEJONG_LIBRARY_CANONICAL_URL,
    }
    target.update(updates)
    return target


def _collect(site: FakeSite, **kwargs: Any):
    return library.collect_sejong_library_courses(
        _target(),
        session_factory=site.session_factory,
        today="2026-08-06",
        max_pages=5,
        detail_limit=10,
        **kwargs,
    )


def test_complete_snapshot_keeps_filter_and_never_calls_private_routes() -> None:
    site = FakeSite()
    rows, parser, meta = _collect(site)

    assert parser == library.SEJONG_LIBRARY_PARSER
    assert [row["provider_course_id"] for row in rows] == [
        f"{library.SEJONG_LIBRARY_PROVIDER}:edusat54:101",
        f"{library.SEJONG_LIBRARY_PROVIDER}:edusat54:99",
    ]
    assert [row["status"] for row in rows] == ["OPEN", "CLOSED"]
    assert rows[0]["application_url"] == rows[0]["raw_url"]
    assert rows[1]["application_url"] == ""
    assert all(row["branch"] == "세종시립도서관" for row in rows)
    assert all(row["address"] == "세종특별자치시 세종로 1207" for row in rows)
    assert all("저장하지 않을 이름" not in str(row) for row in rows)
    assert meta["source_rows"] == meta["declared_source_rows"] == 3
    assert meta["current_count"] == meta["detail_pages"] == 2
    assert meta["expired_count"] == 1
    assert meta["list_requests"] == meta["required_list_requests"] == 5
    assert meta["clamp_verified"] is True
    assert meta["stable_boundaries_verified"] is True
    assert meta["snapshot_complete"] is True
    assert site.page_counts == Counter({1: 2, 2: 2, 3: 1})
    requested = " ".join(url for _kind, url, _params in site.calls)
    assert "user.do" not in requested
    assert "regist.do" not in requested
    assert "down.do" not in requested
    assert "login" not in requested
    assert "private-image" not in requested


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"provider": "WRONG"}, False),
        ({"url": library.SEJONG_LIBRARY_CANONICAL_URL + "&v_status=2"}, False),
        ({"url": library.SEJONG_LIBRARY_CANONICAL_URL.replace("https://", "http://")}, False),
        ({}, True),
    ],
)
def test_exact_provider_and_url_boundary(updates: dict[str, str], expected: bool) -> None:
    assert library.is_sejong_library_target(_target(**updates)) is expected


def test_page_and_detail_caps_fail_atomically() -> None:
    site = FakeSite()
    rows, _parser, meta = library.collect_sejong_library_courses(
        _target(),
        session_factory=site.session_factory,
        today="2026-08-06",
        max_pages=4,
        detail_limit=10,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert site.page_counts == Counter({1: 1})

    site = FakeSite()
    rows, _parser, meta = library.collect_sejong_library_courses(
        _target(),
        session_factory=site.session_factory,
        today="2026-08-06",
        max_pages=5,
        detail_limit=1,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["detail_attempts"] == 0
    assert meta["snapshot_complete"] is False


@pytest.mark.parametrize(
    "site",
    [
        FakeSite(clamp_changed=True),
        FakeSite(stable_first_changed=True),
        FakeSite(detail_changed="99"),
    ],
)
def test_clamp_boundary_and_detail_drift_never_return_partial_rows(site: FakeSite) -> None:
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"]


def test_downstream_dedupe_cannot_shrink_atomic_snapshot() -> None:
    rows, _parser, meta = _collect(FakeSite(), dedupe_rows=lambda values: values[:1])
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "dedupe" in meta["configured_collection_error"]


def test_router_target_operational_and_coverage_bindings(monkeypatch: pytest.MonkeyPatch) -> None:
    from Crawler import Crawler_MunicipalYaml as router

    document = yaml.safe_load(
        (ROOT / "config/crawl_targets/library.yaml").read_text(encoding="utf-8")
    )
    target = next(
        row for row in document["targets"] if row.get("provider") == library.SEJONG_LIBRARY_PROVIDER
    )
    assert target["url"] == library.SEJONG_LIBRARY_CANONICAL_URL
    assert target["service_group"] == "공공강좌"
    assert target["service_group_policy"] == "locked"
    assert (target["collection_category"], target["domain_category"]) == (
        "공공예약",
        "교육·강좌",
    )

    operational = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )["entries"]
    entry = next(row for row in operational if row.get("provider") == library.SEJONG_LIBRARY_PROVIDER)
    assert entry["target_url"] == library.SEJONG_LIBRARY_CANONICAL_URL
    assert entry["row_count"] == 4

    coverage = yaml.safe_load(
        (ROOT / "config/municipal_integrated_reservation_coverage.yaml").read_text(
            encoding="utf-8"
        )
    )["municipalities"]
    sejong = next(row for row in coverage if row["code"] == "3611000000")
    assert library.SEJONG_LIBRARY_PROVIDER in sejong["owner_providers"]
    assert "MUNI_IR_0B0448ACFCED" in sejong["review_candidate_ids"]

    sentinel = ([{"provider_course_id": "sentinel"}], library.SEJONG_LIBRARY_PARSER, {"snapshot_complete": True})
    monkeypatch.setattr(library, "collect_sejong_library_courses", lambda *_args, **_kwargs: sentinel)
    crawl_target = router.CrawlTarget(
        provider=library.SEJONG_LIBRARY_PROVIDER,
        name="세종시립도서관",
        branch="세종시립도서관",
        url=library.SEJONG_LIBRARY_CANONICAL_URL,
        source="test",
    )
    assert router.collect_from_url(
        crawl_target, timeout=3, max_depth=0, max_pages=5, detail_limit=2
    ) == sentinel
