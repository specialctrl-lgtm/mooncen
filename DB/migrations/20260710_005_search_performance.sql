-- Search document and supporting indexes for course/branch keyword queries.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15min';

ALTER TABLE courses ADD COLUMN IF NOT EXISTS search_document TSVECTOR;

CREATE OR REPLACE FUNCTION mooncen_update_course_search_document()
RETURNS TRIGGER AS $$
BEGIN
    NEW.search_document :=
        setweight(to_tsvector('simple', COALESCE(NEW.title, '')), 'A') ||
        setweight(to_tsvector('simple', COALESCE(NEW.category_raw, '')), 'B') ||
        setweight(to_tsvector('simple', COALESCE(NEW.description, '')), 'C') ||
        setweight(to_tsvector('simple', COALESCE(NEW.ai_summary, '')), 'C');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_courses_search_document ON courses;
CREATE TRIGGER trg_courses_search_document
    BEFORE INSERT OR UPDATE OF title, category_raw, description, ai_summary
    ON courses
    FOR EACH ROW EXECUTE FUNCTION mooncen_update_course_search_document();

UPDATE courses
SET search_document =
    setweight(to_tsvector('simple', COALESCE(title, '')), 'A') ||
    setweight(to_tsvector('simple', COALESCE(category_raw, '')), 'B') ||
    setweight(to_tsvector('simple', COALESCE(description, '')), 'C') ||
    setweight(to_tsvector('simple', COALESCE(ai_summary, '')), 'C')
WHERE search_document IS NULL;

CREATE INDEX IF NOT EXISTS idx_courses_search_document ON courses USING gin(search_document);
CREATE INDEX IF NOT EXISTS idx_courses_description_trgm ON courses USING gin(description gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_courses_ai_summary_trgm ON courses USING gin(ai_summary gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_branches_address_trgm ON branches USING gin(address gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_branches_code_trgm ON branches USING gin(branch_code gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_courses_active_popular ON courses(view_count DESC, updated_at DESC) WHERE is_active IS TRUE;
CREATE INDEX IF NOT EXISTS idx_courses_active_created ON courses(created_at DESC) WHERE is_active IS TRUE;
CREATE INDEX IF NOT EXISTS idx_courses_active_deadline ON courses(apply_end ASC NULLS LAST) WHERE is_active IS TRUE;
