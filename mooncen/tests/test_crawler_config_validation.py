from __future__ import annotations

from copy import deepcopy

import pytest

from Crawler import Config


def test_current_crawler_config_is_valid() -> None:
    Config.validate_config()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/courses",
        "https://user:password@example.test/courses",
        "file:///tmp/courses",
        "https://example.test/" + "x" * 2_100,
    ],
)
def test_provider_urls_fail_closed(url: str) -> None:
    providers = deepcopy(Config.PROVIDERS)
    providers["LOTTE"]["base_url"] = url
    with pytest.raises(RuntimeError, match="HTTPS URL without credentials"):
        Config.validate_config(providers=providers)


def test_numeric_and_header_injection_bounds_are_enforced() -> None:
    crawler_config = dict(Config.CRAWLER_CONFIG, concurrent_requests=100)
    with pytest.raises(RuntimeError, match="concurrent_requests"):
        Config.validate_config(crawler_config=crawler_config)
    with pytest.raises(RuntimeError, match="HEADERS"):
        Config.validate_config(headers={"User-Agent": "safe\r\nX-Forged: true"})

