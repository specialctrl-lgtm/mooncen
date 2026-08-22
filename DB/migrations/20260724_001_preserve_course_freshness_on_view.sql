-- A course detail view increments view_count.  That operational counter must
-- not make unchanged crawler content appear freshly collected or edited.
CREATE OR REPLACE FUNCTION update_modified_column()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.view_count IS DISTINCT FROM OLD.view_count
       AND (to_jsonb(NEW) - 'view_count' - 'updated_at')
           IS NOT DISTINCT FROM
           (to_jsonb(OLD) - 'view_count' - 'updated_at') THEN
        NEW.updated_at = OLD.updated_at;
    ELSE
        NEW.updated_at = now();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
