-- Add compact 2/3-gram lexemes for Korean and joined title words. PostgreSQL's
-- simple dictionary otherwise stores "키즈요가" as one token and cannot find
-- the common two-character query "요가" through the GIN search document.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15min';

CREATE OR REPLACE FUNCTION mooncen_search_ngrams(p_text TEXT)
RETURNS TEXT AS $$
DECLARE
    token TEXT;
    token_length INTEGER;
    position INTEGER;
    output TEXT := '';
BEGIN
    FOR token IN
        SELECT match[1]
        FROM regexp_matches(lower(left(COALESCE(p_text, ''), 1000)), '([[:alnum:]가-힣]+)', 'g') AS match
    LOOP
        output := output || ' ' || token;
        token_length := char_length(token);
        IF token_length >= 2 THEN
            FOR position IN 1..(token_length - 1) LOOP
                output := output || ' ' || substring(token FROM position FOR 2);
            END LOOP;
        END IF;
        IF token_length >= 3 THEN
            FOR position IN 1..(token_length - 2) LOOP
                output := output || ' ' || substring(token FROM position FOR 3);
            END LOOP;
        END IF;
    END LOOP;
    RETURN output;
END;
$$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;

CREATE OR REPLACE FUNCTION mooncen_update_course_search_document()
RETURNS TRIGGER AS $$
BEGIN
    NEW.search_document :=
        setweight(to_tsvector('simple', mooncen_search_ngrams(NEW.title)), 'A') ||
        setweight(to_tsvector('simple', mooncen_search_ngrams(NEW.category_raw)), 'B') ||
        setweight(to_tsvector('simple', COALESCE(NEW.description, '')), 'C') ||
        setweight(to_tsvector('simple', COALESCE(NEW.ai_summary, '')), 'C');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

UPDATE courses
SET search_document =
    setweight(to_tsvector('simple', mooncen_search_ngrams(title)), 'A') ||
    setweight(to_tsvector('simple', mooncen_search_ngrams(category_raw)), 'B') ||
    setweight(to_tsvector('simple', COALESCE(description, '')), 'C') ||
    setweight(to_tsvector('simple', COALESCE(ai_summary, '')), 'C');

ANALYZE courses;
ANALYZE branches;
