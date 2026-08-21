from tools.maintenance import propagate_branch_locations as propagation
from tools.maintenance.propagate_branch_locations import (
    LocationMatch,
    build_location_matches,
    choose_unique_verified_source,
    index_verified_sources,
    location_match_key,
    normalize_branch_name,
    persist_matches,
)


def branch(
    branch_id: str,
    name: str,
    *,
    lat: float | None = None,
    lon: float | None = None,
    verified: bool = False,
    confidence: int = 100,
    provider: str = "TEST_PROVIDER",
    address: str = "",
    coordinate_source: str | None = None,
    region_sido: str | None = None,
    region_sigungu: str | None = None,
) -> dict:
    return {
        "id": branch_id,
        "provider": provider,
        "name": name,
        "lat": lat,
        "lon": lon,
        "location_verified": verified,
        "location_confidence": confidence,
        "address": address,
        "coordinate_source": coordinate_source or (
            "KAKAO_LOCAL_ADDRESS" if verified else None
        ),
        "region_sido": region_sido,
        "region_sigungu": region_sigungu,
    }


def test_normalize_branch_name_ignores_spacing_and_case():
    assert normalize_branch_name(" Home Plus 합정점 ") == normalize_branch_name(
        "homeplus합정점"
    )


def test_build_location_matches_uses_the_verified_unique_coordinate():
    targets = [branch("target", "합정 점")]
    sources = [
        branch(
            "source",
            "합정점",
            lat=37.5508451,
            lon=126.9136823,
            verified=True,
            confidence=95,
        ),
        branch(
            "unverified",
            "합정점",
            lat=1.0,
            lon=2.0,
            verified=False,
            confidence=100,
        ),
    ]

    matches, ambiguous, unmatched = build_location_matches(targets, sources)

    assert [match.source["id"] for match in matches] == ["source"]
    assert ambiguous == []
    assert unmatched == []


def test_build_location_matches_rejects_distinct_verified_coordinates():
    targets = [branch("target", "합정점")]
    sources = [
        branch(
            "source-a",
            "합정점",
            lat=37.5508451,
            lon=126.9136823,
            verified=True,
        ),
        branch(
            "source-b",
            "합정점",
            lat=37.5608451,
            lon=126.9236823,
            verified=True,
        ),
    ]

    matches, ambiguous, unmatched = build_location_matches(targets, sources)

    assert matches == []
    assert [row["id"] for row in ambiguous] == ["target"]
    assert unmatched == []


def test_same_name_copy_never_crosses_provider_boundaries():
    target = branch("target", "Shared Facility", provider="FIRST_PROVIDER")
    source = branch(
        "source",
        "Shared Facility",
        provider="SECOND_PROVIDER",
        lat=37.5,
        lon=127.0,
        verified=True,
    )
    indexed = index_verified_sources([source])

    selected, reason = choose_unique_verified_source(
        target,
        indexed.get(location_match_key(target), []),
    )

    assert selected is None
    assert reason == "no_verified_same_name_source"


def test_same_name_copy_never_launders_legacy_google_coordinates():
    target = branch("target", "Shared Facility")
    source = branch(
        "source",
        "Shared Facility",
        lat=37.5,
        lon=127.0,
        verified=True,
        coordinate_source="GOOGLE_GEOCODING",
    )

    selected, reason = choose_unique_verified_source(target, [source])

    assert selected is None
    assert reason == "no_verified_same_name_source"


def test_same_name_copy_rejects_conflicting_address_evidence():
    target = branch(
        "target",
        "Shared Facility",
        address="Seoul Jongno-gu 1",
    )
    source = branch(
        "source",
        "Shared Facility",
        address="Busan Haeundae-gu 2",
        lat=37.5,
        lon=127.0,
        verified=True,
    )

    selected, reason = choose_unique_verified_source(target, [source])

    assert selected is None
    assert reason == "conflicting_target_source_evidence"


def test_same_name_copy_rejects_partial_target_coordinates():
    target = branch("target", "Shared Facility", lat=37.5, lon=None)
    source = branch(
        "source",
        "Shared Facility",
        lat=37.5,
        lon=127.0,
        verified=True,
    )

    selected, reason = choose_unique_verified_source(target, [source])

    assert selected is None
    assert reason == "partial_or_existing_target_coordinates"


def test_persist_relocks_and_revalidates_target_and_source_before_update(monkeypatch):
    target = branch("target", "Shared Facility")
    source = branch(
        "source",
        "Shared Facility",
        lat=37.5,
        lon=127.0,
        verified=True,
    )

    class Cursor:
        def __init__(self):
            self.statements = []
            self.rowcount = 0

        def execute(self, statement, _params):
            self.statements.append(statement)
            if "UPDATE branches" in statement:
                self.rowcount = 1

        def fetchone(self):
            return target

        def fetchall(self):
            return [source]

    cursor = Cursor()

    class CursorContext:
        def __enter__(self):
            return cursor

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(propagation, "get_db_cursor", CursorContext)

    updated = persist_matches([LocationMatch(target=target, source=source)])

    assert updated == 1
    assert len(cursor.statements) == 3
    assert "FOR UPDATE" in cursor.statements[0]
    assert "FOR SHARE" in cursor.statements[1]
    assert "UPDATE branches" in cursor.statements[2]
    assert "lat IS NULL" in cursor.statements[2]
    assert "lon IS NULL" in cursor.statements[2]
    assert "= 'KAKAO_'" in cursor.statements[2]
    assert "geocode_status = 'resolved'" in cursor.statements[2]
