-- Extend compact n-gram coverage to the first 1,000 characters of published
-- descriptions and AI summaries so substring search keeps its former recall.
SET LOCAL lock_timeout = '5s';
-- The production courses table is large enough that this one-time full search
-- document rewrite can exceed 15 minutes even while making steady progress.
SET LOCAL statement_timeout = '30min';

CREATE OR REPLACE FUNCTION mooncen_update_course_search_document()
RETURNS TRIGGER AS $$
BEGIN
    NEW.search_document :=
        setweight(to_tsvector('simple', mooncen_search_ngrams(NEW.title)), 'A') ||
        setweight(to_tsvector('simple', mooncen_search_ngrams(NEW.category_raw)), 'B') ||
        setweight(to_tsvector('simple', mooncen_search_ngrams(NEW.description)), 'C') ||
        setweight(to_tsvector('simple', mooncen_search_ngrams(NEW.ai_summary)), 'C');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

UPDATE courses
SET search_document =
    setweight(to_tsvector('simple', mooncen_search_ngrams(title)), 'A') ||
    setweight(to_tsvector('simple', mooncen_search_ngrams(category_raw)), 'B') ||
    setweight(to_tsvector('simple', mooncen_search_ngrams(description)), 'C') ||
    setweight(to_tsvector('simple', mooncen_search_ngrams(ai_summary)), 'C');

ANALYZE courses;
