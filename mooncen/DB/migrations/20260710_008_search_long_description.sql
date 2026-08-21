-- Preserve exact-token matches beyond the 1,000-character n-gram cap without
-- generating an unbounded number of substring lexemes.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30min';

CREATE OR REPLACE FUNCTION mooncen_update_course_search_document()
RETURNS TRIGGER AS $$
BEGIN
    NEW.search_document :=
        setweight(to_tsvector('simple', mooncen_search_ngrams(NEW.title)), 'A') ||
        setweight(to_tsvector('simple', mooncen_search_ngrams(NEW.category_raw)), 'B') ||
        setweight(to_tsvector('simple', mooncen_search_ngrams(NEW.description)), 'C') ||
        setweight(to_tsvector('simple', COALESCE(NEW.description, '')), 'C') ||
        setweight(to_tsvector('simple', mooncen_search_ngrams(NEW.ai_summary)), 'C') ||
        setweight(to_tsvector('simple', COALESCE(NEW.ai_summary, '')), 'C');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

UPDATE courses
SET search_document =
    setweight(to_tsvector('simple', mooncen_search_ngrams(title)), 'A') ||
    setweight(to_tsvector('simple', mooncen_search_ngrams(category_raw)), 'B') ||
    setweight(to_tsvector('simple', mooncen_search_ngrams(description)), 'C') ||
    setweight(to_tsvector('simple', COALESCE(description, '')), 'C') ||
    setweight(to_tsvector('simple', mooncen_search_ngrams(ai_summary)), 'C') ||
    setweight(to_tsvector('simple', COALESCE(ai_summary, '')), 'C')
WHERE length(COALESCE(description, '')) > 1000
   OR length(COALESCE(ai_summary, '')) > 1000;

ANALYZE courses;
