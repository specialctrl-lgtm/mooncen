from __future__ import annotations

from typing import Any

from Crawler import Crawler_MunicipalYaml as municipal
from Crawler import municipal_geochang
from Crawler import municipal_haman


def _target(provider: str, url: str) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=provider,
        name="상한 라우팅 테스트",
        branch="테스트",
        url=url,
        source="test",
    )


def test_haman_router_clamps_global_limits_to_reviewed_caps(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}

    def fake_collect(*_args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return [], municipal_haman.HAMAN_PARSER, {"no_current_data": True}

    monkeypatch.setattr(
        municipal_haman,
        "collect_haman_education",
        fake_collect,
    )

    municipal.collect_from_url(
        _target(
            municipal_haman.HAMAN_PROVIDER,
            municipal_haman.HAMAN_CANONICAL_URL,
        ),
        max_pages=200,
        detail_limit=3_000,
    )

    assert captured["max_pages"] == municipal_haman.HAMAN_RECOMMENDED_MAX_PAGES
    assert (
        captured["detail_limit"]
        == municipal_haman.HAMAN_RECOMMENDED_DETAIL_LIMIT
    )


def test_geochang_router_clamps_global_limits_to_reviewed_caps(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}

    def fake_collect(*_args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return [], municipal_geochang.GEOCHANG_PARSER, {
            "no_current_data": True
        }

    monkeypatch.setattr(
        municipal_geochang,
        "collect_geochang_education",
        fake_collect,
    )

    municipal.collect_from_url(
        _target(
            municipal_geochang.GEOCHANG_PROVIDER,
            municipal_geochang.GEOCHANG_CANONICAL_URL,
        ),
        max_pages=200,
        detail_limit=3_000,
    )

    assert (
        captured["max_pages"]
        == municipal_geochang.GEOCHANG_RECOMMENDED_MAX_PAGES
    )
    assert (
        captured["detail_limit"]
        == municipal_geochang.GEOCHANG_RECOMMENDED_DETAIL_LIMIT
    )
