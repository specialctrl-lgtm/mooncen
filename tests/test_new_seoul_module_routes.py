from __future__ import annotations

import pytest

from Crawler import Crawler_MunicipalYaml as municipal
from Crawler import (
    municipal_anseong,
    municipal_gangdong,
    municipal_gimpo,
    municipal_guri,
    municipal_guro,
    municipal_michuhol,
    municipal_miryang,
    municipal_mokpo,
    municipal_naju,
    municipal_seogwipo_eticket,
    municipal_seongdong,
    municipal_songpa,
    municipal_wando,
    municipal_yeongcheon,
)


@pytest.mark.parametrize(
    ("module", "collector_name", "provider", "url"),
    (
        (
            municipal_anseong,
            "collect_anseong_education_courses",
            municipal_anseong.ANSEONG_PROVIDER,
            municipal_anseong.ANSEONG_URL,
        ),
        (
            municipal_guro,
            "collect_guro_education_courses",
            municipal_guro.GURO_PROVIDER,
            municipal_guro.GURO_URL,
        ),
        *(
            (
                municipal_gangdong,
                "collect_gangdong_courses",
                provider,
                url,
            )
            for provider, url in municipal_gangdong.GANGDONG_CANONICAL_URLS.items()
        ),
        (
            municipal_gimpo,
            "collect_gimpo_education_courses",
            municipal_gimpo.GIMPO_PROVIDER,
            municipal_gimpo.GIMPO_URL,
        ),
        (
            municipal_guri,
            "collect_guri_education_courses",
            municipal_guri.GURI_GSEEK_PROVIDER,
            municipal_guri.GURI_GSEEK_URL,
        ),
        (
            municipal_guri,
            "collect_guri_education_courses",
            municipal_guri.GURI_RESERVE_PROVIDER,
            municipal_guri.GURI_RESERVE_URL,
        ),
        (
            municipal_michuhol,
            "collect_michuhol_education_courses",
            municipal_michuhol.MICHUHOL_PROVIDER,
            municipal_michuhol.MICHUHOL_URL,
        ),
        (
            municipal_naju,
            "collect_naju_education_courses",
            municipal_naju.NAJU_LIFELONG_PROVIDER,
            municipal_naju.NAJU_LIFELONG_URL,
        ),
        (
            municipal_naju,
            "collect_naju_education_courses",
            municipal_naju.NAJU_GONGIK_PROVIDER,
            municipal_naju.NAJU_GONGIK_URL,
        ),
        (
            municipal_miryang,
            "collect_miryang_education_courses",
            municipal_miryang.MIRYANG_YEYAK_PROVIDER,
            municipal_miryang.MIRYANG_YEYAK_URL,
        ),
        (
            municipal_miryang,
            "collect_miryang_education_courses",
            municipal_miryang.MIRYANG_LIFELONG_PROVIDER,
            municipal_miryang.MIRYANG_LIFELONG_URL,
        ),
        (
            municipal_seongdong,
            "collect_seongdong_integrated_courses",
            municipal_seongdong.SEONGDONG_INTEGRATED_PROVIDER,
            municipal_seongdong.SEONGDONG_INTEGRATED_URL,
        ),
        (
            municipal_seongdong,
            "collect_seongdong_integrated_courses",
            municipal_seongdong.SEONGDONG_INTEGRATED_PROVIDER,
            municipal_seongdong.SEONGDONG_EXPERIENCE_URL,
        ),
        (
            municipal_mokpo,
            "collect_mokpo_education_courses",
            municipal_mokpo.MOKPO_PROVIDER,
            municipal_mokpo.MOKPO_URL,
        ),
        (
            municipal_seogwipo_eticket,
            "collect_seogwipo_eticket_education",
            municipal_seogwipo_eticket.SEOGWIPO_ETICKET_PROVIDER,
            municipal_seogwipo_eticket.SEOGWIPO_ETICKET_TARGET_URL,
        ),
        (
            municipal_songpa,
            "collect_songpa_education_courses",
            municipal_songpa.SONGPA_EDUCATION_PROVIDER,
            municipal_songpa.SONGPA_EDUCATION_URL,
        ),
        *(
            (
                municipal_wando,
                "collect_wando_education",
                source.provider,
                source.url,
            )
            for source in municipal_wando.WANDO_SOURCES
        ),
        (
            municipal_yeongcheon,
            "collect_yeongcheon_education_courses",
            municipal_yeongcheon.YEONGCHEON_PROVIDER,
            municipal_yeongcheon.YEONGCHEON_URL,
        ),
    ),
)
def test_exact_provider_routes_use_the_fail_closed_module(
    monkeypatch: pytest.MonkeyPatch,
    module,
    collector_name: str,
    provider: str,
    url: str,
) -> None:
    sentinel = ([{"title": provider}], f"parser:{provider}", {"snapshot_complete": True})
    monkeypatch.setattr(module, collector_name, lambda *_args, **_kwargs: sentinel)
    target = municipal.CrawlTarget(
        provider=provider,
        name=provider,
        branch="서울특별시",
        url=url,
        source="test",
    )

    assert municipal.collect_from_url(
        target,
        timeout=1,
        max_depth=0,
        max_pages=100,
        detail_limit=1000,
    ) == sentinel


def test_guro_route_injects_the_managed_fetcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def collect(*_args, **kwargs):
        captured.update(kwargs)
        return [], "guro", {"snapshot_complete": True}

    monkeypatch.setattr(municipal_guro, "collect_guro_education_courses", collect)
    target = municipal.CrawlTarget(
        provider=municipal_guro.GURO_PROVIDER,
        name="구로구 통합예약",
        branch="서울특별시 구로구",
        url=municipal_guro.GURO_URL,
        source="test",
    )

    municipal.collect_from_url(
        target,
        timeout=1,
        max_depth=0,
        max_pages=10,
        detail_limit=300,
    )

    assert callable(captured["fetcher"])
    assert callable(captured["session_factory"])


def test_gimpo_route_injects_the_managed_session_and_dedupe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def collect(*_args, **kwargs):
        captured.update(kwargs)
        return [], "gimpo", {"snapshot_complete": True}

    monkeypatch.setattr(municipal_gimpo, "collect_gimpo_education_courses", collect)
    target = municipal.CrawlTarget(
        provider=municipal_gimpo.GIMPO_PROVIDER,
        name="김포시 평생교육",
        branch="경기도 김포시",
        url=municipal_gimpo.GIMPO_URL,
        source="test",
    )

    municipal.collect_from_url(
        target,
        timeout=1,
        max_depth=0,
        max_pages=60,
        detail_limit=400,
    )

    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])
    assert "allow_raw_requests_for_tests" not in captured


def test_gangdong_route_injects_the_managed_session_without_overriding_legacy_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def collect(*_args, **kwargs):
        captured.update(kwargs)
        return [], "gangdong", {"snapshot_complete": True}

    monkeypatch.setattr(municipal_gangdong, "collect_gangdong_courses", collect)
    target = municipal.CrawlTarget(
        provider=municipal_gangdong.GANGDONG_50PLUS_PROVIDER,
        name="강동50플러스센터",
        branch="서울특별시 강동구",
        url=municipal_gangdong.GANGDONG_50PLUS_URL,
        source="test",
    )

    municipal.collect_from_url(
        target,
        timeout=1,
        max_depth=0,
        max_pages=30,
        detail_limit=100,
    )

    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])
    assert "fetcher" not in captured


def test_seogwipo_route_injects_the_managed_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def collect(*_args, **kwargs):
        captured.update(kwargs)
        return [], "seogwipo", {"snapshot_complete": True}

    monkeypatch.setattr(
        municipal_seogwipo_eticket,
        "collect_seogwipo_eticket_education",
        collect,
    )
    target = municipal.CrawlTarget(
        provider=municipal_seogwipo_eticket.SEOGWIPO_ETICKET_PROVIDER,
        name="서귀포시 E-Ticket",
        branch="제주특별자치도 서귀포시",
        url=municipal_seogwipo_eticket.SEOGWIPO_ETICKET_TARGET_URL,
        source="test",
    )

    municipal.collect_from_url(
        target,
        timeout=1,
        max_depth=0,
        max_pages=1,
        detail_limit=100,
    )

    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])
    assert "allow_raw_requests_for_tests" not in captured


@pytest.mark.parametrize(
    ("provider", "url"),
    (
        (municipal_guri.GURI_GSEEK_PROVIDER, municipal_guri.GURI_GSEEK_URL),
        (municipal_guri.GURI_RESERVE_PROVIDER, municipal_guri.GURI_RESERVE_URL),
    ),
)
def test_guri_routes_inject_the_managed_session(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    url: str,
) -> None:
    captured: dict = {}

    def collect(*_args, **kwargs):
        captured.update(kwargs)
        return [], "guri", {"snapshot_complete": True}

    monkeypatch.setattr(municipal_guri, "collect_guri_education_courses", collect)
    target = municipal.CrawlTarget(
        provider=provider,
        name="구리시 교육",
        branch="경기도 구리시",
        url=url,
        source="test",
    )

    municipal.collect_from_url(
        target,
        timeout=1,
        max_depth=0,
        max_pages=60,
        detail_limit=400,
    )

    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])
    assert "fetcher" not in captured
    assert "allow_raw_requests_for_tests" not in captured


@pytest.mark.parametrize(
    ("module", "collector_name", "provider", "url", "max_pages"),
    (
        (
            municipal_anseong,
            "collect_anseong_education_courses",
            municipal_anseong.ANSEONG_PROVIDER,
            municipal_anseong.ANSEONG_URL,
            300,
        ),
        (
            municipal_michuhol,
            "collect_michuhol_education_courses",
            municipal_michuhol.MICHUHOL_PROVIDER,
            municipal_michuhol.MICHUHOL_URL,
            60,
        ),
        (
            municipal_naju,
            "collect_naju_education_courses",
            municipal_naju.NAJU_LIFELONG_PROVIDER,
            municipal_naju.NAJU_LIFELONG_URL,
            130,
        ),
        (
            municipal_naju,
            "collect_naju_education_courses",
            municipal_naju.NAJU_GONGIK_PROVIDER,
            municipal_naju.NAJU_GONGIK_URL,
            10,
        ),
        (
            municipal_miryang,
            "collect_miryang_education_courses",
            municipal_miryang.MIRYANG_YEYAK_PROVIDER,
            municipal_miryang.MIRYANG_YEYAK_URL,
            120,
        ),
        (
            municipal_miryang,
            "collect_miryang_education_courses",
            municipal_miryang.MIRYANG_LIFELONG_PROVIDER,
            municipal_miryang.MIRYANG_LIFELONG_URL,
            30,
        ),
        (
            municipal_mokpo,
            "collect_mokpo_education_courses",
            municipal_mokpo.MOKPO_PROVIDER,
            municipal_mokpo.MOKPO_URL,
            20,
        ),
        (
            municipal_wando,
            "collect_wando_education",
            municipal_wando.WANDO_LIFELONG_PROVIDER,
            municipal_wando.WANDO_LIFELONG_URL,
            10,
        ),
        (
            municipal_yeongcheon,
            "collect_yeongcheon_education_courses",
            municipal_yeongcheon.YEONGCHEON_PROVIDER,
            municipal_yeongcheon.YEONGCHEON_URL,
            120,
        ),
    ),
)
def test_mokpo_wando_and_yeongcheon_routes_inject_managed_network_contracts(
    monkeypatch: pytest.MonkeyPatch,
    module,
    collector_name: str,
    provider: str,
    url: str,
    max_pages: int,
) -> None:
    captured: dict = {}

    def collect(*_args, **kwargs):
        captured.update(kwargs)
        return [], provider, {"snapshot_complete": True}

    monkeypatch.setattr(module, collector_name, collect)
    target = municipal.CrawlTarget(
        provider=provider,
        name=provider,
        branch="테스트 지자체",
        url=url,
        source="test",
    )

    municipal.collect_from_url(
        target,
        timeout=1,
        max_depth=0,
        max_pages=max_pages,
        detail_limit=200,
    )

    assert callable(captured["fetcher"])
    assert callable(captured["session_factory"])
    assert callable(captured["dedupe_rows"])
    assert "allow_raw_requests_for_tests" not in captured
    if module is municipal_anseong:
        assert captured["max_workers"] == 2
