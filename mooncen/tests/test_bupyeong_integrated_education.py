from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import pytest

from Crawler import municipal_bupyeong as bupyeong


@dataclass
class Target:
    provider: str = bupyeong.BUPYEONG_PROVIDER
    url: str = bupyeong.BUPYEONG_URL
    branch: str = "인천광역시 부평구"


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _root_html(*, omit: str = "") -> str:
    links = "".join(
        f'<a href="{item.entry_path}">{item.label}</a>'
        for item in bupyeong.BUPYEONG_CATEGORIES
        if item.key != omit
    )
    return (
        "<html><head><title>인천광역시 부평구 통합예약 서비스</title></head>"
        f"<body>{links}</body></html>"
    )


def _period_labels(category: bupyeong.BupyeongCategory) -> tuple[str, str]:
    if category.key == "elearning":
        return "접수기간", "교육기간"
    return "접수", "교육"


def _list_item(
    category: bupyeong.BupyeongCategory,
    identity: str,
    title: str,
    *,
    status: str = "접수중",
    period: str = "2026-08-01 ~ 2026-09-30",
    apply_period: str = "2026-07-01 ~ 2026-07-31",
    branch: str = "",
) -> str:
    apply_label, period_label = _period_labels(category)
    if category.key == "dong":
        owner = branch or "부평1동"
    elif category.key == "physical":
        owner = "생활체육"
    elif category.key == "woman":
        owner = branch
    else:
        owner = ""
    extra = ""
    if category.key == "lll":
        extra = '<li><span class="wfont">교육장소 :</span> 부평학습관</li>'
    elif category.key != "elearning":
        extra = f'<li><span class="wfont">교육기관 :</span> {owner}</li>'
    return f"""
    <li><div>
      <p class="tit"><a href="{category.detail_path}?lecseq={identity}&amp;sitediv={category.key}&amp;cd=reservation">{title}</a></p>
      <p class="tag_state">{status}</p>
      <ul class="lec_info">
        <li><span class="wfont">{apply_label} :</span> {apply_period}</li>
        {extra}
        <li><span class="wfont">{period_label} :</span> {period}</li>
        <li><span class="wfont">수강료 :</span> 0원</li>
      </ul>
    </div></li>
    """


def _list_html(
    category: bupyeong.BupyeongCategory,
    rows: list[str],
    *,
    selected: int = 1,
    last: int = 1,
) -> str:
    return f"""
    <html><head><title>{category.label} 목록 | 인천광역시 부평구청 통합예약서비스&gt;교육·강좌</title></head>
    <body>
      <ul class="lecList">{''.join(rows)}</ul>
      <p class="paging dp_pc">
        <a class="num select" href="{category.list_path}?sitediv={category.key}&amp;cd=reservation&amp;nowPage={selected}">{selected}</a>
        <a class="page_btn btn_last" href="{category.list_path}?sitediv={category.key}&amp;cd=reservation&amp;nowPage={last}">마지막 페이지</a>
      </p>
    </body></html>
    """


def _detail_pairs(category: bupyeong.BupyeongCategory) -> list[tuple[str, str]]:
    pairs = [
        ("교육대상", "누구나"),
        ("접수방법", "온라인"),
        ("접수기간", "온라인 : 2026-07-01 09:00:00 ~ 2026-07-31 18:00:00"),
        ("교육기간", "2026-08-01 ~ 2026-09-30 화 10:00~12:00"),
        ("수 강 료", "무료 (재료비 : 무료)"),
        ("문의전화", "032-509-0000"),
        ("안내", "공식 안내"),
    ]
    if category.key == "elearning":
        pairs.extend(
            [
                ("교육요일", "월,화"),
                ("접수인원", "0 / 24 명"),
                ("강의내용", "공식 강의내용"),
            ]
        )
    else:
        owner = "부평1동" if category.key == "dong" else (
            "생활체육" if category.key == "physical" else ""
        )
        pairs.extend(
            [
                ("교육기관", owner),
                ("추첨방법", "선착순"),
                ("신청정원", "온라인 : 10 명"),
                ("교육장소", "공식 교육장"),
                ("강사", "홍길동"),
            ]
        )
    return pairs


def _detail_html(
    category: bupyeong.BupyeongCategory,
    identity: str,
    title: str,
    *,
    include_application: bool = True,
    overrides: dict[str, str] | None = None,
) -> str:
    pairs = _detail_pairs(category)
    replacements = overrides or {}
    rendered = "".join(
        f"<dt>{label}</dt><dd>{replacements.get(label, value)}</dd>"
        for label, value in pairs
    )
    application = (
        f'<a href="{category.application_path}?lecseq={identity}&amp;sitediv={category.key}&amp;cd=reservation">신청</a>'
        if include_application
        else ""
    )
    return f"""
    <html><head><title>{category.label} 내용 | 인천광역시 부평구청 통합예약서비스&gt;교육·강좌</title></head>
    <body><div class="board_view"><div class="title"><p>{title}</p></div><dl>{rendered}</dl>{application}</div></body></html>
    """


def _legacy_detail_html(category: bupyeong.BupyeongCategory, identity: str, title: str) -> str:
    return f"""
    <html><head><title>{category.label} 내용 | 인천광역시 부평구청 통합예약서비스&gt;교육·강좌</title></head>
    <body><div class="board_view"><div class="title"><p>{title}</p></div><dl>
      <dt>교육기간</dt><dd>~</dd><dt>접수기간</dt><dd>방문 : ~</dd>
    </dl></div></body></html>
    """


def _default_specs() -> dict[str, dict[str, Any]]:
    return {
        "dong": {
            "identity": "9001",
            "title": "주민 강좌",
            "status": "접수중",
            "period": "2026-08-01 ~ 2026-09-30",
            "branch": "부평1동",
        },
        "lll": {
            "identity": "9002",
            "title": "평생 강좌",
            "status": "접수중",
            "period": "2026-08-01 ~ 2026-09-30",
        },
        "elearning": {
            "identity": "9003",
            "title": "백운 1기 컴퓨터",
            "status": "접수예정",
            "period": "2026-08-01 ~ 2026-09-30",
        },
        "physical": {
            "identity": "9004",
            "title": "생활체육 강좌",
            "status": "접수중",
            "period": "2026-08-01 ~ 2026-09-30",
        },
        "woman": {
            "identity": "9005",
            "title": "여성센터 과거 강좌",
            "status": "접수마감",
            "period": "2026-01-01 ~ 2026-02-01",
        },
    }


class Harness:
    def __init__(
        self,
        *,
        specs: dict[str, dict[str, Any]] | None = None,
        root: str | None = None,
        page_factory: Callable[[bupyeong.BupyeongCategory, int, int], str] | None = None,
        detail_factory: Callable[[bupyeong.BupyeongCategory, str, dict[str, Any]], str] | None = None,
    ) -> None:
        self.specs = specs or _default_specs()
        self.root = root or _root_html()
        self.page_factory = page_factory
        self.detail_factory = detail_factory
        self.calls: list[str] = []
        self.sessions: list[FakeSession] = []
        self.page_calls: Counter[tuple[str, int]] = Counter()

    def session_factory(self) -> FakeSession:
        current = FakeSession()
        self.sessions.append(current)
        return current

    def _page(self, category: bupyeong.BupyeongCategory, requested: int) -> str:
        self.page_calls[(category.key, requested)] += 1
        if self.page_factory is not None:
            return self.page_factory(
                category, requested, self.page_calls[(category.key, requested)]
            )
        spec = self.specs[category.key]
        row = _list_item(
            category,
            spec["identity"],
            spec["title"],
            status=spec["status"],
            period=spec["period"],
            branch=spec.get("branch", ""),
        )
        return _list_html(category, [row], selected=1, last=1)

    def _detail(self, category: bupyeong.BupyeongCategory, identity: str) -> str:
        spec = self.specs[category.key]
        if self.detail_factory is not None:
            return self.detail_factory(category, identity, spec)
        return _detail_html(
            category,
            identity,
            spec["title"],
            include_application=spec["status"] == "접수중",
        )

    def fetcher(self, _session: Any, url: str, _timeout: int) -> BeautifulSoup:
        self.calls.append(url)
        if url == bupyeong.BUPYEONG_URL:
            return _soup(self.root)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for category in bupyeong.BUPYEONG_CATEGORIES:
            if (
                parsed.path == category.list_path
                and query.get("sitediv") == [category.key]
            ):
                return _soup(self._page(category, int(query["nowPage"][0])))
            if (
                parsed.path == category.detail_path
                and query.get("sitediv") == [category.key]
            ):
                return _soup(self._detail(category, query["lecseq"][0]))
        raise AssertionError(f"unexpected URL: {url}")


def _collect(harness: Harness, **kwargs: Any):
    return bupyeong.collect_bupyeong_education_courses(
        Target(),
        timeout=3,
        max_pages=kwargs.pop("max_pages", 10),
        detail_limit=kwargs.pop("detail_limit", 20),
        today=kwargs.pop("today", "2026-07-20"),
        max_workers=kwargs.pop("max_workers", 1),
        fetcher=harness.fetcher,
        session_factory=harness.session_factory,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        ("WRONG", bupyeong.BUPYEONG_URL),
        (bupyeong.BUPYEONG_PROVIDER, "http://www.icbp.go.kr/reservation/"),
        (bupyeong.BUPYEONG_PROVIDER, "https://www.icbp.go.kr/reservation"),
        (bupyeong.BUPYEONG_PROVIDER, "https://www.icbp.go.kr/reservation/?x=1"),
        (bupyeong.BUPYEONG_PROVIDER, "https://user@www.icbp.go.kr/reservation/"),
        (bupyeong.BUPYEONG_PROVIDER, "https://www.icbp.go.kr.evil.test/reservation/"),
    ],
)
def test_target_boundary_is_exact(provider: str, url: str) -> None:
    assert not bupyeong.is_target(Target(provider=provider, url=url))
    assert bupyeong.is_target(Target())


def test_url_builders_are_canonical_and_reject_bad_identity() -> None:
    category = bupyeong.BUPYEONG_CATEGORIES[0]
    assert bupyeong.bupyeong_list_url(category, 2) == (
        "https://www.icbp.go.kr/lecture/lectureList.do?"
        "sitediv=dong&cd=reservation&nowPage=2"
    )
    assert bupyeong.bupyeong_detail_url("dong", "123") == (
        "https://www.icbp.go.kr/lecture/lectureDetail.do?"
        "lecseq=123&sitediv=dong&cd=reservation"
    )
    assert bupyeong.bupyeong_detail_url("dong", "123&admin=1") == ""


def test_complete_five_catalogue_snapshot_enriches_current_rows() -> None:
    harness = Harness()
    rows, parser, meta = _collect(harness)

    assert parser == bupyeong.BUPYEONG_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["sentinel_mode"] == "last_page_clamp"
    assert meta["source_count"] == 5
    assert meta["source_total"] == 5
    assert meta["pages"] == 5
    assert meta["request_count"] == 20
    assert meta["current_count"] == 4
    assert meta["returned_count"] == 4
    assert meta["detail_pages"] == 4
    assert meta["sentinel_requests"] == 5
    assert meta["page_one_rechecks"] == 5
    assert {row["branch"] for row in rows} == {
        "부평1동",
        "부평구평생학습관",
        "백운 e-배움터",
        "부평구 생활체육",
    }
    by_id = {row["raw_fields"]["lecseq"]: row for row in rows}
    assert by_id["9001"]["provider_course_id"].endswith(":lecture:9001")
    assert by_id["9001"]["application_type"] == "ONLINE_RESERVATION"
    assert by_id["9003"].get("application_url") is None
    assert by_id["9003"]["status"] == "SCHEDULED"
    assert all(row["period"] == "2026-08-01 ~ 2026-09-30" for row in rows)
    assert all(session.closed for session in harness.sessions)


def test_invalid_target_fails_before_any_network_access() -> None:
    called = False

    def session_factory() -> FakeSession:
        nonlocal called
        called = True
        return FakeSession()

    rows, _, meta = bupyeong.collect_bupyeong_education_courses(
        Target(url="https://www.icbp.go.kr/reservation/?x=1"),
        session_factory=session_factory,
    )
    assert rows == []
    assert called is False
    assert "exact Bupyeong" in meta["configured_collection_error"]


def test_root_must_expose_all_five_official_education_owners() -> None:
    harness = Harness(root=_root_html(omit="woman"))
    rows, _, meta = _collect(harness)
    assert rows == []
    assert "ownership/navigation" in meta["configured_collection_error"]


def test_page_cap_is_aggregate_across_all_five_catalogues() -> None:
    harness = Harness()
    rows, _, meta = _collect(harness, max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "below required 5 data pages" in meta["configured_collection_error"]
    assert not any("lectureDetail.do" in value for value in harness.calls)


def test_detail_cap_suppresses_the_entire_snapshot() -> None:
    harness = Harness()
    rows, _, meta = _collect(harness, detail_limit=3)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "below required 4" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


def test_last_page_clamp_must_repeat_the_exact_last_page_ids() -> None:
    specs = _default_specs()

    def pages(category: bupyeong.BupyeongCategory, requested: int, _call: int) -> str:
        spec = specs[category.key]
        identity = "999999" if category.key == "dong" and requested == 2 else spec["identity"]
        row = _list_item(
            category,
            identity,
            spec["title"],
            status=spec["status"],
            period=spec["period"],
            branch=spec.get("branch", ""),
        )
        return _list_html(category, [row], selected=1, last=1)

    rows, _, meta = _collect(Harness(specs=specs, page_factory=pages))
    assert rows == []
    assert "last-page clamp rows changed" in meta["configured_collection_error"]


def test_page_one_change_during_detail_traversal_fails_closed() -> None:
    specs = _default_specs()

    def pages(category: bupyeong.BupyeongCategory, requested: int, call: int) -> str:
        spec = specs[category.key]
        identity = spec["identity"]
        if category.key == "dong" and requested == 1 and call == 2:
            identity = "999998"
        row = _list_item(
            category,
            identity,
            spec["title"],
            status=spec["status"],
            period=spec["period"],
            branch=spec.get("branch", ""),
        )
        return _list_html(category, [row], selected=1, last=1)

    rows, _, meta = _collect(Harness(specs=specs, page_factory=pages))
    assert rows == []
    assert "page 1 changed during traversal" in meta["configured_collection_error"]


def test_nonlast_short_page_is_not_a_complete_catalogue() -> None:
    specs = _default_specs()

    def pages(category: bupyeong.BupyeongCategory, requested: int, _call: int) -> str:
        spec = specs[category.key]
        row = _list_item(
            category,
            spec["identity"],
            spec["title"],
            status=spec["status"],
            period=spec["period"],
            branch=spec.get("branch", ""),
        )
        if category.key == "dong":
            selected = min(requested, 2)
            return _list_html(category, [row], selected=selected, last=2)
        return _list_html(category, [row], selected=1, last=1)

    rows, _, meta = _collect(
        Harness(specs=specs, page_factory=pages), max_pages=6
    )
    assert rows == []
    assert "exposed 1, expected 12" in meta["configured_collection_error"]


def test_unknown_branch_and_unknown_status_are_fail_closed() -> None:
    specs = _default_specs()
    specs["dong"] = {**specs["dong"], "branch": "계양1동"}
    rows, _, meta = _collect(Harness(specs=specs))
    assert rows == []
    assert "unknown resident-centre branch" in meta["configured_collection_error"]

    specs = _default_specs()
    specs["physical"] = {**specs["physical"], "status": "신청가능"}
    rows, _, meta = _collect(Harness(specs=specs))
    assert rows == []
    assert "unknown status" in meta["configured_collection_error"]


def test_unapproved_undated_record_is_not_silently_expired() -> None:
    specs = _default_specs()
    specs["dong"] = {
        **specs["dong"],
        "identity": "9999",
        "status": "접수마감",
        "period": "~",
    }
    rows, _, meta = _collect(Harness(specs=specs))
    assert rows == []
    assert "unapproved undated or invalid-date course" in meta["configured_collection_error"]


def test_exact_legacy_undated_closed_record_is_verified_but_not_returned() -> None:
    specs = _default_specs()
    for key, spec in list(specs.items()):
        specs[key] = {
            **spec,
            "status": "접수마감",
            "period": "2026-01-01 ~ 2026-02-01",
        }
    specs["dong"] = {
        **specs["dong"],
        "identity": "14512",
        "title": "영어회화",
        "period": "~",
    }

    def details(category: bupyeong.BupyeongCategory, identity: str, spec: dict[str, Any]) -> str:
        if identity == "14512":
            return _legacy_detail_html(category, identity, spec["title"])
        raise AssertionError("expired dated rows must not request details")

    rows, _, meta = _collect(Harness(specs=specs, detail_factory=details))
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["historical_invalid_count"] == 1
    assert meta["detail_required_count"] == 1
    assert meta["no_current_data"] is True


@pytest.mark.parametrize(
    "mode",
    ["title", "period", "branch", "application", "fetch"],
)
def test_any_current_detail_failure_discards_all_rows(mode: str) -> None:
    harness: Harness

    def details(category: bupyeong.BupyeongCategory, identity: str, spec: dict[str, Any]) -> str:
        if category.key != "dong":
            return _detail_html(
                category,
                identity,
                spec["title"],
                include_application=spec["status"] == "접수중",
            )
        if mode == "fetch":
            raise RuntimeError("network failure")
        title = "바뀐 제목" if mode == "title" else spec["title"]
        overrides: dict[str, str] = {}
        if mode == "period":
            overrides["교육기간"] = "2026-08-02 ~ 2026-09-30"
        if mode == "branch":
            overrides["교육기관"] = "부평2동"
        return _detail_html(
            category,
            identity,
            title,
            include_application=mode != "application",
            overrides=overrides,
        )

    harness = Harness(detail_factory=details)
    rows, _, meta = _collect(harness)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"]


def test_cross_catalogue_lecseq_collision_is_rejected() -> None:
    specs = _default_specs()
    specs["lll"] = {**specs["lll"], "identity": specs["dong"]["identity"]}
    rows, _, meta = _collect(Harness(specs=specs))
    assert rows == []
    assert "duplicate lecseq across five catalogues" in meta["configured_collection_error"]


def test_router_dedupe_may_not_shrink_a_complete_source_snapshot() -> None:
    harness = Harness()
    rows, _, meta = _collect(harness, dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed complete row count" in meta["configured_collection_error"]


def test_complete_all_past_catalogues_return_an_explicit_empty_snapshot() -> None:
    specs = _default_specs()
    for key, spec in list(specs.items()):
        specs[key] = {
            **spec,
            "status": "접수마감",
            "period": "2026-01-01 ~ 2026-02-01",
        }
    harness = Harness(specs=specs)
    rows, _, meta = _collect(harness)
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["current_count"] == 0
    assert meta["no_current_data"] is True
    assert meta["detail_attempts"] == 0


def test_closed_course_may_keep_reception_dates_only_on_the_list() -> None:
    specs = _default_specs()
    specs["dong"] = {**specs["dong"], "status": "접수마감"}

    def details(category: bupyeong.BupyeongCategory, identity: str, spec: dict[str, Any]) -> str:
        overrides = {"접수방법": "", "접수기간": ""} if category.key == "dong" else {}
        return _detail_html(
            category,
            identity,
            spec["title"],
            include_application=spec["status"] == "접수중",
            overrides=overrides,
        )

    rows, _, meta = _collect(Harness(specs=specs, detail_factory=details))
    assert meta["snapshot_complete"] is True
    dong = next(row for row in rows if row["raw_fields"]["category_key"] == "dong")
    assert dong["apply_period"] == "2026-07-01 ~ 2026-07-31"
    assert dong.get("application_url") is None


def test_compact_yyyymmdd_history_is_parsed_without_weakening_current_dates() -> None:
    specs = _default_specs()
    for key, spec in list(specs.items()):
        specs[key] = {
            **spec,
            "status": "접수마감",
            "period": "20190101 ~ 20190331",
        }
    rows, _, meta = _collect(Harness(specs=specs))
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["historical_invalid_count"] == 0


def test_elearning_detail_fields_may_use_the_official_data_cell_layout() -> None:
    def details(category: bupyeong.BupyeongCategory, identity: str, spec: dict[str, Any]) -> str:
        if category.key != "elearning":
            return _detail_html(
                category,
                identity,
                spec["title"],
                include_application=spec["status"] == "접수중",
            )
        rendered = "".join(
            f"<dt>{label}</dt><dd>{value}</dd>"
            for label, value in _detail_pairs(category)
        )
        return f"""
        <html><head><title>{category.label} 내용 | 인천광역시 부평구청 통합예약서비스&gt;교육·강좌</title></head>
        <body><div class="board_view"><div class="title"><p>{spec['title']}</p></div></div>
        <div class="data_cell"><dl>{rendered}</dl></div></body></html>
        """

    rows, _, meta = _collect(Harness(detail_factory=details))
    assert meta["snapshot_complete"] is True
    assert any(row["branch"] == "백운 e-배움터" for row in rows)
