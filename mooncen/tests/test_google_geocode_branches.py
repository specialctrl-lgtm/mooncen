"""Compatibility contract for the retired Google geocoder entry point."""

from tools.maintenance import google_geocode_branches as legacy_geocoder
from tools.maintenance import kakao_geocode_branches as kakao_geocoder


def test_legacy_geocoder_entry_point_delegates_to_kakao() -> None:
    assert legacy_geocoder.main is kakao_geocoder.main
    assert legacy_geocoder.GeocodeCandidate is kakao_geocoder.GeocodeCandidate
    assert legacy_geocoder.KAKAO_ADDRESS_SEARCH_URL == (
        kakao_geocoder.KAKAO_ADDRESS_SEARCH_URL
    )
