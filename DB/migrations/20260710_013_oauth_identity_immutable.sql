-- OAuth provider identity and verification provenance are immutable after enrollment.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '5min';

CREATE OR REPLACE FUNCTION mooncen_protect_oauth_identity()
RETURNS trigger AS $$
BEGIN
    IF NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.provider IS DISTINCT FROM OLD.provider
       OR NEW.provider_user_id IS DISTINCT FROM OLD.provider_user_id
       OR NEW.email IS DISTINCT FROM OLD.email
       OR NEW.email_verified IS DISTINCT FROM OLD.email_verified THEN
        RAISE EXCEPTION 'OAuth identity fields are immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_protect_oauth_identity ON oauth_accounts;
CREATE TRIGGER trg_protect_oauth_identity
    BEFORE UPDATE ON oauth_accounts
    FOR EACH ROW
    EXECUTE FUNCTION mooncen_protect_oauth_identity();
