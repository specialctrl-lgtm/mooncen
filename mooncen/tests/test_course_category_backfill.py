from __future__ import annotations

from contextlib import contextmanager

from tools.maintenance import backfill_course_categories as backfill


def test_category_repair_queries_render_the_damage_condition(monkeypatch) -> None:
    queries: list[str] = []

    class Cursor:
        def execute(self, query, params) -> None:
            rendered = str(query)
            assert "{repair_condition}" not in rendered
            queries.append(rendered)
            if "set_config" in rendered:
                assert params == ("120000ms",)

        def fetchone(self):
            return {"count": 1}

    @contextmanager
    def fake_cursor():
        yield Cursor()

    monkeypatch.setattr(backfill, "get_db_cursor", fake_cursor)

    matched, updated = backfill.backfill(
        {
            "PROVIDER": {
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
            }
        },
        dry_run=False,
    )

    assert (matched, updated) == (1, 1)
    assert len(queries) == 3
    repair_queries = queries[1:]
    assert all("collection_category" in query for query in repair_queries)
    assert all("domain_category" in query for query in repair_queries)
    assert all("regexp_replace" in query for query in repair_queries)


def test_provider_repair_condition_only_targets_available_category_fields() -> None:
    collection_only = backfill.provider_repair_condition(
        {"collection_category": "공공예약"}
    )
    both = backfill.provider_repair_condition(
        {
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
        }
    )

    assert "collection_category" in collection_only
    assert "domain_category" not in collection_only
    assert "NULLIF" in collection_only
    assert "collection_category" in both
    assert "domain_category" in both
    assert backfill.provider_repair_condition({}) == ""
