from types import SimpleNamespace

import pytest

from backend.routers.seo_pages import course_offer_json_ld
from utils.fee_semantics import fee_status


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "UNKNOWN"),
        ("", "UNKNOWN"),
        (-1, "UNKNOWN"),
        (0, "FREE"),
        ("0", "FREE"),
        (12_000, "PAID"),
    ],
)
def test_fee_status_keeps_unknown_distinct_from_free(value, expected) -> None:
    assert fee_status(value) == expected


def test_unknown_fee_is_omitted_from_seo_offer_instead_of_published_as_free() -> None:
    unknown = SimpleNamespace(
        fee=None,
        status="OPEN",
        end_date=None,
        apply_end=None,
        apply_start=None,
    )
    free = SimpleNamespace(
        fee=0,
        status="OPEN",
        end_date=None,
        apply_end=None,
        apply_start=None,
    )

    unknown_offer = course_offer_json_ld(unknown, "https://mooncen.kr/course/unknown")
    free_offer = course_offer_json_ld(free, "https://mooncen.kr/course/free")

    assert "price" not in unknown_offer
    assert "priceCurrency" not in unknown_offer
    assert free_offer["price"] == 0.0
    assert free_offer["priceCurrency"] == "KRW"
