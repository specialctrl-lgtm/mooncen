from __future__ import annotations

from dataclasses import dataclass
import inspect
from pathlib import Path
from typing import Any

import yaml
import pytest

from Crawler import municipal_gunsan as gunsan


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class GunsanTarget:
    provider: str = gunsan.GUNSAN_PROVIDER
    url: str = gunsan.GUNSAN_CANONICAL_URL
    branch: str = gunsan.GUNSAN_MUNICIPALITY_NAME


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _factory() -> tuple[Any, list[DummySession]]:
    sessions: list[DummySession] = []

    def make_session() -> DummySession:
        current = DummySession()
        sessions.append(current)
        return current

    return make_session, sessions


def _table(headers: tuple[str, ...], rows: list[str]) -> str:
    return (
        "<table class='borad_skin'><tr>"
        + "".join(f"<th>{header}</th>" for header in headers)
        + "</tr>"
        + "".join(rows)
        + "</table>"
    )


def _pager(page: int, last: int, source: gunsan.GunsanSource) -> str:
    if last <= 1:
        return ""
    values = []
    for value in range(1, last + 1):
        if value == page:
            values.append(f"<li class='on'>{value}</li>")
        else:
            values.append(
                f"<li><a href='{source.path}?page={value}'>{value}</a></li>"
            )
    return "<div class='bbspage'><ul>" + "".join(values) + "</ul></div>"


def _standard_row(
    source: gunsan.GunsanSource,
    identity: str,
    sequence: int | None,
    title: str,
    *,
    current: bool = True,
    source_status: str = "진행",
    active_application: bool = False,
) -> str:
    if current:
        application = "07-01 ~ 07-30" if active_application else "06-01 ~ 06-30"
        period = f"교육:07-21 ~ 08-31 신청:{application}"
    else:
        period = "교육:06-01 ~ 06-02 신청:05-01 ~ 05-31"
    detail = gunsan.gunsan_detail_url(source, identity)
    cells = [source_status, f"<a href='{detail}'>{title}</a>", "군산시민", "월 (10:00 ~ 12:00)", period, "온라인 : 1/20"]
    if sequence is not None:
        cells.insert(0, str(sequence))
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def _detail_html(
    source: gunsan.GunsanSource,
    identity: str,
    title: str,
    *,
    current: bool = True,
    active_application: bool = False,
    missing_venue: bool = False,
) -> str:
    education = (
        "2099-07-21 ~ 2099-08-31"
        if current
        else "2099-06-01 ~ 2099-06-02"
    )
    apply_period = (
        "2099-07-01 ~ 2099-07-30"
        if active_application
        else ("2099-06-01 ~ 2099-06-30" if current else "2099-05-01 ~ 2099-05-31")
    )
    fields = [
        ("강좌명", title),
        ("수강료", "0원"),
        ("모집정원", "1 / 20 명"),
        ("강의기간", education),
        ("접수기간", apply_period),
        ("강좌요일", "월"),
    ]
    if not missing_venue:
        fields.append(("교육장소", source.name))
    rows = "".join(f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in fields)
    application = ""
    if active_application:
        query = [("pm", "input"), ("idx", identity)]
        if source.detail_m:
            query.append(("m", source.detail_m))
        from urllib.parse import urlencode

        application = (
            f"<a href='{source.path}?{urlencode(query)}'>수강신청</a>"
        )
    return f"""
      <html><body>
        <nav><a href='/program/lecture2018.php?m=160100'>신청하기</a></nav>
        <table class='borad_list'>{rows}</table>
        <table class='contents'><tr><th>강좌 상세내용</th><td>{title} 상세 교육과정</td></tr></table>
        {application}
      </body></html>
    """


def _empty_page(source: gunsan.GunsanSource, year: int = 2099) -> str:
    if source.kind == "gunsanhak":
        headers = (f"{year} 강좌", "일자", "강사명", "직위 및 강의주제")
    else:
        headers = source.headers
    rows = (
        [f"<tr><td colspan='{len(headers)}'>검색 결과가 없습니다.</td></tr>"]
        if source.kind == "culture"
        else []
    )
    return _table(headers, rows)


def _fixture(
    *,
    bad_osicdo_sentinel: bool = False,
    duplicate_identity: bool = False,
    missing_detail_venue: bool = False,
    gunsanhak_active_row: bool = False,
    empty: bool = False,
) -> tuple[Any, Any, list[DummySession], dict[str, str]]:
    pages: dict[str, str] = {}
    identities: dict[str, str] = {}
    sources = {source.code: source for source in gunsan.GUNSAN_SOURCES}

    for source in gunsan.GUNSAN_SOURCES:
        variants = ("1", "2") if source.kind == "culture" else ("",)
        for variant in variants:
            first = gunsan.gunsan_list_url(
                source, 1, variant=variant, reference_year=2099
            )
            sentinel = gunsan.gunsan_list_url(
                source, 2, variant=variant, reference_year=2099
            )
            pages[first] = _empty_page(source)
            pages[sentinel] = _empty_page(source)

    if not empty:
        central = sources["central"]
        central_id = "900000000001"
        identities["central"] = central_id
        central_row = _standard_row(
            central,
            central_id,
            1,
            "중앙 평생학습 강좌",
            active_application=True,
        )
        central_url = gunsan.gunsan_list_url(
            central, 1, reference_year=2099
        )
        pages[central_url] = _table(central.headers, [central_row])
        pages[gunsan.gunsan_detail_url(central, central_id)] = _detail_html(
            central,
            central_id,
            "중앙 평생학습 강좌",
            active_application=True,
            missing_venue=missing_detail_venue,
        )

        osicdo = sources["osicdo"]
        first_rows = []
        for sequence in range(1, 11):
            identity = f"9100000000{sequence:02d}"
            identities[f"osicdo_{sequence}"] = identity
            first_rows.append(
                _standard_row(
                    osicdo,
                    central_id if duplicate_identity and sequence == 1 else identity,
                    sequence,
                    f"오식도 강좌 {sequence}",
                )
            )
            actual_identity = central_id if duplicate_identity and sequence == 1 else identity
            pages[gunsan.gunsan_detail_url(osicdo, actual_identity)] = _detail_html(
                osicdo, actual_identity, f"오식도 강좌 {sequence}"
            )
        last_id = "910000000011"
        identities["osicdo_11"] = last_id
        second_row = _standard_row(osicdo, last_id, 11, "오식도 강좌 11")
        pages[gunsan.gunsan_detail_url(osicdo, last_id)] = _detail_html(
            osicdo, last_id, "오식도 강좌 11"
        )
        page1 = gunsan.gunsan_list_url(osicdo, 1, reference_year=2099)
        page2 = gunsan.gunsan_list_url(osicdo, 2, reference_year=2099)
        page3 = gunsan.gunsan_list_url(osicdo, 3, reference_year=2099)
        pages[page1] = _table(osicdo.headers, first_rows) + _pager(1, 2, osicdo)
        pages[page2] = _table(osicdo.headers, [second_row]) + _pager(2, 2, osicdo)
        pages[page3] = (
            _table(
                osicdo.headers,
                [_standard_row(osicdo, "910000000012", 12, "잘못된 종단 강좌")],
            )
            if bad_osicdo_sentinel
            else _empty_page(osicdo)
        )

        semangm = sources["semangm"]
        expired_id = "920000000001"
        identities["expired"] = expired_id
        expired_row = _standard_row(
            semangm,
            expired_id,
            None,
            "종료된 새만금 강좌",
            current=False,
            source_status="종료",
        )
        semangm_url = gunsan.gunsan_list_url(
            semangm, 1, reference_year=2099
        )
        pages[semangm_url] = _table(semangm.headers, [expired_row])
        pages[gunsan.gunsan_detail_url(semangm, expired_id)] = _detail_html(
            semangm, expired_id, "종료된 새만금 강좌", current=False
        )

    if gunsanhak_active_row:
        source = sources["gunsanhak"]
        first = gunsan.gunsan_list_url(source, 1, reference_year=2099)
        headers = ("2099 강좌", "일자", "강사명", "직위 및 강의주제")
        pages[first] = _table(
            headers,
            ["<tr><td>제1강</td><td>2099-08-01</td><td>강사</td><td>주제</td></tr>"],
        )

    calls: list[str] = []

    def fetch(_session: Any, url: str, _timeout: int) -> str:
        calls.append(url)
        if url not in pages:
            raise AssertionError(f"unexpected GET {url}")
        return pages[url]

    fetch.calls = calls  # type: ignore[attr-defined]
    make_session, sessions = _factory()
    return fetch, make_session, sessions, identities


def test_target_routes_sources_and_stale_notice_exclusion() -> None:
    assert gunsan.is_gunsan_education_target(GunsanTarget())
    assert gunsan.is_gunsan_education_target(
        {"provider": gunsan.GUNSAN_PROVIDER, "url": gunsan.GUNSAN_LEGACY_ENTRY_URL}
    )
    assert not gunsan.is_gunsan_education_target(
        {
            "provider": "MUNI_WWW_GUNSAN_GO_KR_FF0982F2",
            "url": gunsan.GUNSAN_EXCLUDED_STALE_NOTICE_URLS[0],
        }
    )
    assert gunsan.is_gunsan_excluded_notice_target(
        {"url": gunsan.GUNSAN_EXCLUDED_STALE_NOTICE_URLS[0]}
    )
    assert {source.code for source in gunsan.GUNSAN_SOURCES} == {
        "central",
        "wolmyeong",
        "osicdo",
        "semangm",
        "gunsanhak",
        "future",
        "minju",
        "culture_cafe",
    }
    assert gunsan.gunsan_list_url(
        next(source for source in gunsan.GUNSAN_SOURCES if source.code == "osicdo"),
        2,
        reference_year=2099,
    ).endswith("?page=2")


def test_target_manifest_promotes_canonical_and_disables_stale_notice() -> None:
    document = yaml.safe_load(
        (ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml").read_text(
            encoding="utf-8"
        )
    )
    targets = {row["provider"]: row for row in document["targets"]}

    canonical = targets[gunsan.GUNSAN_PROVIDER]
    assert canonical["url"] == gunsan.GUNSAN_CANONICAL_URL
    assert canonical["branch"] == gunsan.GUNSAN_MUNICIPALITY_NAME
    assert canonical["municipality_code"] == gunsan.GUNSAN_MUNICIPALITY_CODE
    assert canonical["municipality_full_name"] == gunsan.GUNSAN_MUNICIPALITY_NAME
    assert canonical["source_group"] == "municipal_reservation"
    assert canonical["origin"] == "live_validated"
    assert canonical["crawler_status"] == "ready"
    assert canonical["full_snapshot_required"] is True
    assert gunsan.GUNSAN_LEGACY_ENTRY_URL in canonical["ownership_aliases"]

    stale = next(
        row
        for row in document["targets"]
        if row.get("url") in gunsan.GUNSAN_EXCLUDED_STALE_NOTICE_URLS
    )
    assert stale["source_group"] == "excluded"
    assert stale["crawler_status"] == "excluded_url_shape"
    assert stale["superseded_by"] == gunsan.GUNSAN_PROVIDER
    assert stale["last_quality"]["collected"] == 0


def test_shared_router_dispatches_with_managed_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from Crawler import Crawler_MunicipalYaml as router

    captured: dict[str, Any] = {}
    sentinel = ([{"title": "routed"}], gunsan.GUNSAN_PARSER, {"snapshot_complete": True})

    def fake_collect(target: Any, **kwargs: Any) -> tuple[Any, ...]:
        captured["target"] = target
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(gunsan, "collect_gunsan_education_courses", fake_collect)
    target = router.CrawlTarget(
        provider=gunsan.GUNSAN_PROVIDER,
        name="군산시 평생학습정보망 통합 교육강좌",
        branch=gunsan.GUNSAN_MUNICIPALITY_NAME,
        url=gunsan.GUNSAN_CANONICAL_URL,
        source="test",
    )

    assert router.collect_from_url(
        target,
        timeout=7,
        max_depth=0,
        max_pages=37,
        detail_limit=91,
    ) == sentinel
    assert captured["target"] is target
    assert captured["timeout"] == 7
    assert captured["max_pages"] == 37
    assert captured["detail_limit"] == 91
    assert callable(captured["fetcher"])
    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])


def test_complete_fanout_sentinels_all_details_and_status_contract() -> None:
    fetch, make_session, sessions, _ = _fixture()
    rows, parser, meta = gunsan.collect_gunsan_education_courses(
        GunsanTarget(),
        timeout=7,
        max_pages=100,
        detail_limit=100,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-20",
        dedupe_rows=lambda values: values,
    )

    assert parser == gunsan.GUNSAN_PARSER
    assert len(rows) == 12
    assert meta["source_total"] == meta["detail_pages"] == 13
    assert meta["current_count"] == 12
    assert meta["expired_count"] == 1
    assert meta["required_list_requests"] == meta["pages"] == 19
    assert meta["source_counts"]["central"] == 1
    assert meta["source_counts"]["osicdo"] == 11
    assert meta["source_counts"]["semangm"] == 1
    assert meta["page_counts"]["osicdo:1"] == 10
    assert meta["page_counts"]["osicdo:2"] == 1
    assert meta["page_counts"]["osicdo:3"] == 0
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert meta["application_open_count"] == 1

    open_rows = [row for row in rows if row["status"] == "OPEN"]
    assert len(open_rows) == 1
    assert "pm=input" in open_rows[0]["application_url"]
    assert open_rows[0]["application_url"] != open_rows[0]["raw_url"]
    closed_rows = [row for row in rows if row["status"] == "CLOSED"]
    assert len(closed_rows) == 11
    assert all(not row["application_url"] for row in closed_rows)
    assert all(row["branch"] == gunsan.GUNSAN_MUNICIPALITY_NAME for row in rows)
    assert all(row["municipality_full_name"] == "전북특별자치도 군산시" for row in rows)
    assert all(row["period"].startswith("2099-") for row in rows)
    assert len({row["provider_course_id"] for row in rows}) == len(rows)
    assert len({row["raw_url"] for row in rows}) == len(rows)
    assert all(session.closed for session in sessions)


def test_bad_sentinel_duplicate_identity_and_detail_contract_fail_closed() -> None:
    fetch, make_session, _, _ = _fixture(bad_osicdo_sentinel=True)
    rows, _, meta = gunsan.collect_gunsan_education_courses(
        GunsanTarget(), max_pages=100, detail_limit=100,
        fetcher=fetch, session_factory=make_session, today="2099-07-20"
    )
    assert rows == []
    assert "sentinel page is not empty" in meta["configured_collection_error"]

    fetch, make_session, _, _ = _fixture(duplicate_identity=True)
    rows, _, meta = gunsan.collect_gunsan_education_courses(
        GunsanTarget(), max_pages=100, detail_limit=100,
        fetcher=fetch, session_factory=make_session, today="2099-07-20"
    )
    assert rows == []
    assert meta["duplicate_identity_count"] == 1

    fetch, make_session, _, _ = _fixture(missing_detail_venue=True)
    rows, _, meta = gunsan.collect_gunsan_education_courses(
        GunsanTarget(), max_pages=100, detail_limit=100,
        fetcher=fetch, session_factory=make_session, today="2099-07-20"
    )
    assert rows == []
    assert "missing detail keys 교육장소" in meta["configured_collection_error"]


def test_caps_unsupported_gunsanhak_rows_and_injection_fail_closed() -> None:
    fetch, make_session, _, _ = _fixture()
    rows, _, meta = gunsan.collect_gunsan_education_courses(
        GunsanTarget(), max_pages=9, detail_limit=100,
        fetcher=fetch, session_factory=make_session, today="2099-07-20"
    )
    assert rows == []
    assert meta["source_cap_reached"] is True

    fetch, make_session, _, _ = _fixture(gunsanhak_active_row=True)
    rows, _, meta = gunsan.collect_gunsan_education_courses(
        GunsanTarget(), max_pages=100, detail_limit=100,
        fetcher=fetch, session_factory=make_session, today="2099-07-20"
    )
    assert rows == []
    assert "gunsanhak" in meta["configured_collection_error"]
    assert "unsupported catalogue rows" in meta["configured_collection_error"]

    rows, _, meta = gunsan.collect_gunsan_education_courses(GunsanTarget())
    assert rows == []
    assert "managed fetcher" in meta["configured_collection_error"]


def test_complete_empty_fixed_fanout_is_authoritative() -> None:
    fetch, make_session, sessions, _ = _fixture(empty=True)
    rows, _, meta = gunsan.collect_gunsan_education_courses(
        GunsanTarget(), max_pages=100, detail_limit=100,
        fetcher=fetch, session_factory=make_session, today="2099-07-20"
    )
    assert rows == []
    assert meta["source_total"] == meta["detail_pages"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["configured_collection_error"] == ""
    assert all(session.closed for session in sessions)


def test_transport_and_api_safety_contract() -> None:
    source = inspect.getsource(gunsan)
    assert "verify=False" not in source
    assert "verify = False" not in source
    assert "allow_redirects=True" not in source
    parameters = inspect.signature(
        gunsan.collect_gunsan_education_courses
    ).parameters
    assert parameters["max_pages"].default == 200
    assert parameters["detail_limit"].default == 2000
