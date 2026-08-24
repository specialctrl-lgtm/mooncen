from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

from backend.routers.courses import _course_keyword_filter


ROOT = Path(__file__).resolve().parents[1]


def test_course_keyword_filter_uses_indexed_search_document():
    sql = str(
        _course_keyword_filter("요가").compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "courses.search_document @@ websearch_to_tsquery('simple', '요가')" in sql
    assert "courses.description ILIKE" not in sql
    assert "courses.ai_summary ILIKE" not in sql
    assert "branches.name ILIKE" in sql
    assert "branches.address ILIKE" in sql


def test_course_keyword_filter_rejects_expensive_single_character_search():
    with pytest.raises(ValueError, match="at least two"):
        _course_keyword_filter("요")


def test_search_schema_has_trigger_and_supporting_indexes():
    sql = (ROOT / "DB" / "schema.sql").read_text(encoding="utf-8")

    assert "CREATE OR REPLACE FUNCTION mooncen_search_ngrams" in sql
    assert "CREATE TRIGGER trg_courses_search_document" in sql
    assert "idx_courses_search_document" in sql
    assert "idx_courses_active_popular" in sql
    assert "idx_courses_active_created" in sql
    assert "idx_courses_active_deadline" in sql


def test_incremental_search_migrations_preserve_long_text_and_korean_substrings():
    migration_dir = ROOT / "DB" / "migrations"
    ngrams = (migration_dir / "20260710_006_korean_search_ngrams.sql").read_text(encoding="utf-8")
    descriptions = (migration_dir / "20260710_007_search_description_ngrams.sql").read_text(encoding="utf-8")
    long_text = (migration_dir / "20260710_008_search_long_description.sql").read_text(encoding="utf-8")

    assert "substring(token FROM position FOR 2)" in ngrams
    assert "mooncen_search_ngrams(NEW.description)" in descriptions
    assert "COALESCE(NEW.description, '')" in long_text
    assert "length(COALESCE(description, '')) > 1000" in long_text
