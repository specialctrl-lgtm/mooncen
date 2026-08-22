-- Append-only Crawler Studio drafts and source revisions.
-- Apply only after 20260812_002_release_action_requests.sql on the marked
-- crawler-control database. This migration intentionally grants no execution,
-- build, signing, artifact, rollout, worker, or primary-data capability.

DO $$
BEGIN
    IF current_setting('server_encoding') <> 'UTF8'
       OR to_regclass('public.ops_crawler_control_database_marker') IS NULL
       OR to_regclass('public.ops_crawler_api_bindings') IS NULL
       OR to_regprocedure('public.current_crawler_api_environment()') IS NULL
       OR to_regprocedure('public.digest(bytea,text)') IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM ops_crawler_control_database_marker
           WHERE singleton IS TRUE AND database_name = current_database()::name
       ) THEN
        RAISE EXCEPTION 'crawler studio requires the marked, API-bound crawler-control database and pgcrypto digest';
    END IF;
END;
$$;

CREATE TABLE ops_crawler_studio_provider_paths (
    provider TEXT NOT NULL,
    source_path TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT pk_ops_crawler_studio_provider_paths PRIMARY KEY (provider, source_path),
    CONSTRAINT chk_ops_crawler_studio_provider CHECK (provider ~ '^[A-Z][A-Z0-9_]{0,99}$'),
    CONSTRAINT chk_ops_crawler_studio_source_path CHECK (
        source_path = btrim(source_path)
        AND char_length(source_path) BETWEEN 12 AND 240
        AND source_path ~ '^Crawler/[A-Za-z0-9_./-]+[.]py$'
        AND source_path !~ '(^|/)[.][.]?(/|$)'
        AND source_path !~ '[\\]'
    )
);

-- This is a reviewed, checksummed snapshot. Browser/API callers receive no
-- INSERT/UPDATE/DELETE privilege on the allowlist.
INSERT INTO ops_crawler_studio_provider_paths (provider, source_path) VALUES
    ('AK_PLAZA', 'Crawler/Crawler_YamlSources.py'),
    ('ANYANG_LIFELONG_LEARNING', 'Crawler/generated_yaml/ANYANG_LIFELONG_LEARNING.py'),
    ('BABSANG_WELFARE_PROGRAM', 'Crawler/Crawler_BabsangWelfare.py'),
    ('BUSAN_RESERVATION', 'Crawler/generated_yaml/BUSAN_RESERVATION.py'),
    ('DAEGU_RESERVATION', 'Crawler/generated_yaml/DAEGU_RESERVATION.py'),
    ('DAEJEON_OK_RESERVATION', 'Crawler/generated_yaml/DAEJEON_OK_RESERVATION.py'),
    ('ELAND_RETAIL', 'Crawler/Crawler_YamlSources.py'),
    ('EMART', 'Crawler/Crawler_Emart.py'),
    ('EXPERIENCE_TARGETS', 'Crawler/Crawler_EducationExperience.py'),
    ('GALLERIA', 'Crawler/Crawler_YamlSources.py'),
    ('GWANGJU_RESERVATION', 'Crawler/generated_yaml/GWANGJU_RESERVATION.py'),
    ('HOMEPLUS', 'Crawler/Crawler_Homeplus.py'),
    ('HYUNDAI_DEPT', 'Crawler/Crawler_YamlSources.py'),
    ('INCHEON_RESERVATION', 'Crawler/generated_yaml/INCHEON_RESERVATION.py'),
    ('LOTTE', 'Crawler/Crawler_Lotte.py'),
    ('LOTTE_MART', 'Crawler/Crawler_YamlSources.py'),
    ('MUNICIPAL_RESERVATION_TARGETS', 'Crawler/Crawler_MunicipalIntegratedReservation.py'),
    ('MUNI_DOKSEODANG_SD_GO_KR_A8C20229', 'Crawler/generated_yaml/MUNI_DOKSEODANG_SD_GO_KR_A8C20229.py'),
    ('MUNI_JANGAN_SUWON_GO_KR_D82A0EAE', 'Crawler/generated_yaml/MUNI_JANGAN_SUWON_GO_KR_D82A0EAE.py'),
    ('MUNI_LEARNING_SUWON_GO_KR_3AF2DB76', 'Crawler/generated_yaml/MUNI_LEARNING_SUWON_GO_KR_3AF2DB76.py'),
    ('MUNI_LEARNING_SUWON_GO_KR_402954DA', 'Crawler/generated_yaml/MUNI_LEARNING_SUWON_GO_KR_402954DA.py'),
    ('MUNI_LEARNING_SUWON_GO_KR_6ABE3488', 'Crawler/generated_yaml/MUNI_LEARNING_SUWON_GO_KR_6ABE3488.py'),
    ('MUNI_LEARNING_SUWON_GO_KR_A915395E', 'Crawler/generated_yaml/MUNI_LEARNING_SUWON_GO_KR_A915395E.py'),
    ('MUNI_MBIS_POHANG_GO_KR_0407D99A', 'Crawler/generated_yaml/MUNI_MBIS_POHANG_GO_KR_0407D99A.py'),
    ('MUNI_ORG_JJE_GO_KR_3205C1E8', 'Crawler/generated_yaml/MUNI_ORG_JJE_GO_KR_3205C1E8.py'),
    ('MUNI_PALDAL_SUWON_GO_KR_7F5BC8C6', 'Crawler/generated_yaml/MUNI_PALDAL_SUWON_GO_KR_7F5BC8C6.py'),
    ('MUNI_PALDAL_SUWON_GO_KR_D78BD1B4', 'Crawler/generated_yaml/MUNI_PALDAL_SUWON_GO_KR_D78BD1B4.py'),
    ('MUNI_SEJONG_NL_GO_KR_7F55E25D', 'Crawler/generated_yaml/MUNI_SEJONG_NL_GO_KR_7F55E25D.py'),
    ('MUNI_SUGANG_SEONGNAM_GO_KR_4D24781E', 'Crawler/generated_yaml/MUNI_SUGANG_SEONGNAM_GO_KR_4D24781E.py'),
    ('MUNI_SUGANG_SEONGNAM_GO_KR_D447262D', 'Crawler/generated_yaml/MUNI_SUGANG_SEONGNAM_GO_KR_D447262D.py'),
    ('MUNI_SUGANG_SEONGNAM_GO_KR_FAA99A7B', 'Crawler/generated_yaml/MUNI_SUGANG_SEONGNAM_GO_KR_FAA99A7B.py'),
    ('MUNI_WWW_DAEDEOK_GO_KR_360B9B7C', 'Crawler/generated_yaml/MUNI_WWW_DAEDEOK_GO_KR_360B9B7C.py'),
    ('MUNI_WWW_DANGJIN_GO_KR_3C378AA6', 'Crawler/generated_yaml/MUNI_WWW_DANGJIN_GO_KR_3C378AA6.py'),
    ('MUNI_WWW_GONGJU_GO_KR_7CBA2D38', 'Crawler/generated_yaml/MUNI_WWW_GONGJU_GO_KR_7CBA2D38.py'),
    ('MUNI_WWW_GURO_GO_KR_A4A5D3E3', 'Crawler/generated_yaml/MUNI_WWW_GURO_GO_KR_A4A5D3E3.py'),
    ('MUNI_WWW_YANGJU_GO_KR_1A2AECAC', 'Crawler/generated_yaml/MUNI_WWW_YANGJU_GO_KR_1A2AECAC.py'),
    ('MUNI_YEYAK_SYF_OR_KR_7D3E2EF5', 'Crawler/generated_yaml/MUNI_YEYAK_SYF_OR_KR_7D3E2EF5.py'),
    ('SEONGNAM_BAEUMSOOP', 'Crawler/Crawler_SeongnamBaeumsoop.py'),
    ('SEOSAN_WELFARE_TOTAL_RESERVATION', 'Crawler/Crawler_GeneratedYamlTargets.py'),
    ('SEOUL_PUBLIC_SERVICE', 'Crawler/Crawler_SeoulPublicService.py'),
    ('SHINSEGAE_ACADEMY', 'Crawler/Crawler_YamlSources.py'),
    ('YONGIN_LIFELONG_LEARNING', 'Crawler/Crawler_YonginLifelong.py');

DO $$
DECLARE
    allowlist_count INTEGER;
    allowlist_digest TEXT;
BEGIN
    SELECT count(*),
           encode(public.digest(convert_to(
               string_agg(provider || E'\t' || source_path, E'\n' ORDER BY provider, source_path),
               'UTF8'
           ), 'sha256'), 'hex')
    INTO allowlist_count, allowlist_digest
    FROM public.ops_crawler_studio_provider_paths;
    IF allowlist_count <> 42
       OR allowlist_digest <> '873172712aac8dd01e919864fece65b662f47adf0d4f9f0d404ce4bbebe350f4' THEN
        RAISE EXCEPTION 'crawler studio reviewed provider/path snapshot differs';
    END IF;
END;
$$;

CREATE TABLE ops_crawler_studio_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    environment TEXT NOT NULL,
    provider TEXT NOT NULL,
    source_path TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    latest_revision INTEGER NOT NULL DEFAULT 0,
    created_by UUID NOT NULL,
    created_login NAME NOT NULL DEFAULT session_user,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ux_ops_crawler_studio_draft_path UNIQUE (environment, source_path),
    CONSTRAINT fk_ops_crawler_studio_draft_path FOREIGN KEY (provider, source_path)
        REFERENCES ops_crawler_studio_provider_paths (provider, source_path)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_ops_crawler_studio_draft_api_binding FOREIGN KEY (created_login, environment)
        REFERENCES ops_crawler_api_bindings (database_login, environment)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT chk_ops_crawler_studio_draft_environment
        CHECK (environment IN ('production', 'staging', 'development')),
    CONSTRAINT chk_ops_crawler_studio_draft_title
        CHECK (title = btrim(title) AND char_length(title) BETWEEN 3 AND 160),
    CONSTRAINT chk_ops_crawler_studio_draft_status
        CHECK (status IN ('draft', 'in_review', 'approved', 'changes_requested', 'archived')),
    CONSTRAINT chk_ops_crawler_studio_draft_revision CHECK (latest_revision >= 0)
);

CREATE TABLE ops_crawler_studio_revisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    draft_id UUID NOT NULL REFERENCES ops_crawler_studio_drafts(id) ON DELETE RESTRICT,
    environment TEXT NOT NULL,
    revision INTEGER NOT NULL,
    impacted_providers TEXT[] NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_size_bytes INTEGER NOT NULL,
    source_text TEXT NOT NULL,
    change_summary TEXT NOT NULL,
    created_by UUID NOT NULL,
    created_login NAME NOT NULL DEFAULT session_user,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ux_ops_crawler_studio_revision UNIQUE (draft_id, revision),
    CONSTRAINT fk_ops_crawler_studio_revision_api_binding
        FOREIGN KEY (created_login, environment)
        REFERENCES ops_crawler_api_bindings (database_login, environment)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT chk_ops_crawler_studio_revision_environment
        CHECK (environment IN ('production', 'staging', 'development')),
    CONSTRAINT chk_ops_crawler_studio_revision_number CHECK (revision > 0),
    CONSTRAINT chk_ops_crawler_studio_revision_impacted_providers CHECK (
        cardinality(impacted_providers) BETWEEN 1 AND 42
        AND array_position(impacted_providers, NULL) IS NULL
    ),
    CONSTRAINT chk_ops_crawler_studio_revision_sha
        CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_crawler_studio_revision_size
        CHECK (source_size_bytes BETWEEN 1 AND 524288),
    CONSTRAINT chk_ops_crawler_studio_revision_source
        CHECK (octet_length(source_text) = source_size_bytes),
    CONSTRAINT chk_ops_crawler_studio_revision_summary
        CHECK (change_summary = btrim(change_summary) AND char_length(change_summary) BETWEEN 3 AND 500)
);

CREATE TABLE ops_crawler_studio_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    draft_id UUID NOT NULL REFERENCES ops_crawler_studio_drafts(id) ON DELETE RESTRICT,
    environment TEXT NOT NULL,
    revision INTEGER NOT NULL,
    decision TEXT NOT NULL,
    comment TEXT NOT NULL,
    reviewed_by UUID NOT NULL,
    reviewer_login NAME NOT NULL DEFAULT session_user,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT fk_ops_crawler_studio_review_revision
        FOREIGN KEY (draft_id, revision)
        REFERENCES ops_crawler_studio_revisions (draft_id, revision)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_ops_crawler_studio_review_api_binding
        FOREIGN KEY (reviewer_login, environment)
        REFERENCES ops_crawler_api_bindings (database_login, environment)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT chk_ops_crawler_studio_review_environment
        CHECK (environment IN ('production', 'staging', 'development')),
    CONSTRAINT chk_ops_crawler_studio_review_decision
        CHECK (decision IN ('submit', 'approve', 'request_changes', 'archive')),
    CONSTRAINT chk_ops_crawler_studio_review_comment
        CHECK (comment = btrim(comment) AND char_length(comment) BETWEEN 3 AND 1000)
);

CREATE INDEX idx_ops_crawler_studio_drafts_environment
    ON ops_crawler_studio_drafts (environment, updated_at DESC, id DESC);
CREATE INDEX idx_ops_crawler_studio_revisions_draft
    ON ops_crawler_studio_revisions (draft_id, revision DESC);
CREATE INDEX idx_ops_crawler_studio_reviews_draft
    ON ops_crawler_studio_reviews (draft_id, created_at DESC, id DESC);

-- These objects are new in this ledgered migration (plain CREATE above), so
-- recording their canonical server representation cannot bless a pre-existing
-- weaker constraint. Later drop/recreate drift loses this binding even when an
-- attacker or accident reuses the reviewed name and type.
DO $$
DECLARE
    constraint_row record;
    contract_comment TEXT;
BEGIN
    FOR constraint_row IN
        SELECT namespace_row.nspname, table_row.relname,
               constraint_item.conname, constraint_item.oid,
               pg_get_constraintdef(constraint_item.oid) AS definition
        FROM pg_constraint constraint_item
        JOIN pg_class table_row ON table_row.oid = constraint_item.conrelid
        JOIN pg_namespace namespace_row ON namespace_row.oid = table_row.relnamespace
        WHERE constraint_item.conrelid IN (
            'public.ops_crawler_studio_provider_paths'::regclass,
            'public.ops_crawler_studio_drafts'::regclass,
            'public.ops_crawler_studio_revisions'::regclass,
            'public.ops_crawler_studio_reviews'::regclass
        ) AND constraint_item.contype IN ('p', 'u', 'f', 'c')
    LOOP
        contract_comment := 'mooncen-crawler-studio-constraint-v1:sha256:' ||
            encode(public.digest(convert_to(
                constraint_row.definition, 'UTF8'
            ), 'sha256'), 'hex');
        EXECUTE format(
            'COMMENT ON CONSTRAINT %I ON %I.%I IS %L',
            constraint_row.conname, constraint_row.nspname,
            constraint_row.relname, contract_comment
        );
    END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_crawler_studio_append_only()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'crawler studio evidence is append-only';
END;
$$;
REVOKE ALL ON FUNCTION enforce_crawler_studio_append_only() FROM PUBLIC;

DROP TRIGGER IF EXISTS zz_ops_crawler_studio_paths_immutable
    ON ops_crawler_studio_provider_paths;
CREATE TRIGGER zz_ops_crawler_studio_paths_immutable
    BEFORE UPDATE OR DELETE ON ops_crawler_studio_provider_paths
    FOR EACH ROW EXECUTE FUNCTION enforce_crawler_studio_append_only();
DROP TRIGGER IF EXISTS zz_ops_crawler_studio_revisions_immutable
    ON ops_crawler_studio_revisions;
CREATE TRIGGER zz_ops_crawler_studio_revisions_immutable
    BEFORE UPDATE OR DELETE ON ops_crawler_studio_revisions
    FOR EACH ROW EXECUTE FUNCTION enforce_crawler_studio_append_only();
DROP TRIGGER IF EXISTS zz_ops_crawler_studio_reviews_immutable
    ON ops_crawler_studio_reviews;
CREATE TRIGGER zz_ops_crawler_studio_reviews_immutable
    BEFORE UPDATE OR DELETE ON ops_crawler_studio_reviews
    FOR EACH ROW EXECUTE FUNCTION enforce_crawler_studio_append_only();

CREATE OR REPLACE FUNCTION enforce_crawler_studio_draft_transition()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE latest_decision TEXT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.environment := public.current_crawler_api_environment();
        NEW.created_login := session_user::name;
        NEW.status := 'draft';
        NEW.latest_revision := 0;
        NEW.created_at := clock_timestamp();
        NEW.updated_at := NEW.created_at;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'crawler drafts are append-only';
    END IF;
    IF ROW(NEW.id, NEW.environment, NEW.provider, NEW.source_path,
           NEW.title, NEW.created_by, NEW.created_login, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.environment, OLD.provider, OLD.source_path,
           OLD.title, OLD.created_by, OLD.created_login, OLD.created_at) THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'crawler draft identity is immutable';
    END IF;
    IF NEW.latest_revision = OLD.latest_revision + 1 THEN
        IF NEW.status <> 'draft' OR NOT EXISTS (
            SELECT 1
            FROM public.ops_crawler_studio_revisions revision
            WHERE revision.draft_id = OLD.id
              AND revision.environment = OLD.environment
              AND revision.revision = NEW.latest_revision
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'crawler draft revision evidence is invalid';
        END IF;
    ELSIF NEW.latest_revision = OLD.latest_revision
          AND NEW.status IS DISTINCT FROM OLD.status THEN
        SELECT review.decision INTO latest_decision
        FROM public.ops_crawler_studio_reviews review
        WHERE review.draft_id = OLD.id
          AND review.environment = OLD.environment
          AND review.revision = OLD.latest_revision
        ORDER BY review.created_at DESC, review.id DESC
        LIMIT 1;
        IF NOT (
            (OLD.status IN ('draft', 'changes_requested')
             AND NEW.status = 'in_review' AND latest_decision = 'submit')
            OR (OLD.status = 'in_review'
                AND NEW.status = 'approved' AND latest_decision = 'approve')
            OR (OLD.status = 'in_review'
                AND NEW.status = 'changes_requested' AND latest_decision = 'request_changes')
            OR (OLD.status <> 'archived'
                AND NEW.status = 'archived' AND latest_decision = 'archive')
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'crawler draft review transition is invalid';
        END IF;
    ELSIF NEW.latest_revision IS DISTINCT FROM OLD.latest_revision
          OR NEW.status IS DISTINCT FROM OLD.status THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'crawler draft revision fence is invalid';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION enforce_crawler_studio_draft_transition() FROM PUBLIC;
DROP TRIGGER IF EXISTS zz_ops_crawler_studio_draft_transition
    ON ops_crawler_studio_drafts;
CREATE TRIGGER zz_ops_crawler_studio_draft_transition
    BEFORE INSERT OR UPDATE OR DELETE ON ops_crawler_studio_drafts
    FOR EACH ROW EXECUTE FUNCTION enforce_crawler_studio_draft_transition();

CREATE OR REPLACE FUNCTION enforce_crawler_studio_revision_insert()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE
    parent_environment TEXT;
    parent_latest_revision INTEGER;
    parent_status TEXT;
    parent_source_path TEXT;
    expected_impacted_providers TEXT[];
BEGIN
    SELECT draft.environment, draft.latest_revision, draft.status, draft.source_path
    INTO parent_environment, parent_latest_revision, parent_status, parent_source_path
    FROM public.ops_crawler_studio_drafts draft
    WHERE draft.id = NEW.draft_id
    FOR UPDATE;
    IF parent_environment IS NULL OR parent_environment <> public.current_crawler_api_environment() THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'crawler revision environment is unavailable';
    END IF;
    IF NEW.revision <> parent_latest_revision + 1
       OR parent_status NOT IN ('draft', 'changes_requested', 'approved', 'archived') THEN
        RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'crawler revision optimistic fence conflicts';
    END IF;
    SELECT array_agg(path.provider ORDER BY path.provider)
    INTO expected_impacted_providers
    FROM public.ops_crawler_studio_provider_paths path
    WHERE path.source_path = parent_source_path;
    IF cardinality(expected_impacted_providers) IS NULL
       OR cardinality(expected_impacted_providers) = 0 THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
            MESSAGE = 'crawler revision has no reviewed impacted providers';
    END IF;
    NEW.environment := parent_environment;
    -- The caller cannot choose or narrow this immutable impact snapshot.
    NEW.impacted_providers := expected_impacted_providers;
    NEW.created_login := session_user::name;
    NEW.created_at := clock_timestamp();
    IF encode(public.digest(convert_to(NEW.source_text, 'UTF8'), 'sha256'), 'hex') <> NEW.source_sha256
       OR octet_length(NEW.source_text) <> NEW.source_size_bytes THEN
        RAISE EXCEPTION USING ERRCODE = '22000', MESSAGE = 'crawler revision source digest differs';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION enforce_crawler_studio_revision_insert() FROM PUBLIC;
DROP TRIGGER IF EXISTS zz_ops_crawler_studio_revision_insert
    ON ops_crawler_studio_revisions;
CREATE TRIGGER zz_ops_crawler_studio_revision_insert
    BEFORE INSERT ON ops_crawler_studio_revisions
    FOR EACH ROW EXECUTE FUNCTION enforce_crawler_studio_revision_insert();

CREATE OR REPLACE FUNCTION enforce_crawler_studio_review_insert()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE
    parent_environment TEXT;
    parent_status TEXT;
BEGIN
    IF NEW.decision = 'approve' THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'independent crawler source approval evidence is not implemented';
    END IF;
    SELECT draft.environment, draft.status INTO parent_environment, parent_status
    FROM public.ops_crawler_studio_drafts draft
    WHERE draft.id = NEW.draft_id AND draft.latest_revision = NEW.revision
    FOR UPDATE;
    IF parent_environment IS NULL OR parent_environment <> public.current_crawler_api_environment() THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'crawler review revision is not current';
    END IF;
    IF NOT (
        (NEW.decision = 'submit' AND parent_status IN ('draft', 'changes_requested'))
        OR (NEW.decision IN ('approve', 'request_changes') AND parent_status = 'in_review')
        OR (NEW.decision = 'archive' AND parent_status <> 'archived')
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'crawler review decision is invalid for current status';
    END IF;
    NEW.environment := parent_environment;
    NEW.reviewer_login := session_user::name;
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION enforce_crawler_studio_review_insert() FROM PUBLIC;
DROP TRIGGER IF EXISTS zz_ops_crawler_studio_review_insert
    ON ops_crawler_studio_reviews;
CREATE TRIGGER zz_ops_crawler_studio_review_insert
    BEFORE INSERT ON ops_crawler_studio_reviews
    FOR EACH ROW EXECUTE FUNCTION enforce_crawler_studio_review_insert();

CREATE OR REPLACE FUNCTION enforce_crawler_studio_draft_commit()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM public.ops_crawler_studio_drafts draft
        JOIN public.ops_crawler_studio_revisions revision
          ON revision.draft_id = draft.id
         AND revision.environment = draft.environment
         AND revision.revision = draft.latest_revision
        WHERE draft.id = NEW.id
          AND draft.environment = NEW.environment
          AND draft.latest_revision > 0
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'crawler draft has no attached revision';
    END IF;
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION enforce_crawler_studio_draft_commit() FROM PUBLIC;
DROP TRIGGER IF EXISTS zz_ops_crawler_studio_draft_commit
    ON ops_crawler_studio_drafts;
CREATE CONSTRAINT TRIGGER zz_ops_crawler_studio_draft_commit
    AFTER INSERT ON ops_crawler_studio_drafts
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION enforce_crawler_studio_draft_commit();

CREATE OR REPLACE FUNCTION enforce_crawler_studio_revision_commit()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM public.ops_crawler_studio_drafts draft
        WHERE draft.id = NEW.draft_id
          AND draft.environment = NEW.environment
          AND draft.latest_revision >= NEW.revision
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'crawler revision was not attached to its draft';
    END IF;
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION enforce_crawler_studio_revision_commit() FROM PUBLIC;
DROP TRIGGER IF EXISTS zz_ops_crawler_studio_revision_commit
    ON ops_crawler_studio_revisions;
CREATE CONSTRAINT TRIGGER zz_ops_crawler_studio_revision_commit
    AFTER INSERT ON ops_crawler_studio_revisions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION enforce_crawler_studio_revision_commit();

CREATE OR REPLACE FUNCTION enforce_crawler_studio_review_commit()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE expected_status TEXT;
BEGIN
    expected_status := CASE NEW.decision
        WHEN 'submit' THEN 'in_review'
        WHEN 'approve' THEN 'approved'
        WHEN 'request_changes' THEN 'changes_requested'
        WHEN 'archive' THEN 'archived'
    END;
    IF NOT EXISTS (
        SELECT 1
        FROM public.ops_crawler_studio_drafts draft
        WHERE draft.id = NEW.draft_id
          AND draft.environment = NEW.environment
          AND draft.latest_revision = NEW.revision
          AND draft.status = expected_status
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'crawler review was not applied to its draft';
    END IF;
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION enforce_crawler_studio_review_commit() FROM PUBLIC;
DROP TRIGGER IF EXISTS zz_ops_crawler_studio_review_commit
    ON ops_crawler_studio_reviews;
CREATE CONSTRAINT TRIGGER zz_ops_crawler_studio_review_commit
    AFTER INSERT ON ops_crawler_studio_reviews
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION enforce_crawler_studio_review_commit();

ALTER TABLE ops_crawler_studio_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops_crawler_studio_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops_crawler_studio_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops_crawler_studio_drafts FORCE ROW LEVEL SECURITY;
ALTER TABLE ops_crawler_studio_revisions FORCE ROW LEVEL SECURITY;
ALTER TABLE ops_crawler_studio_reviews FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS crawler_studio_draft_acl_access ON ops_crawler_studio_drafts;
CREATE POLICY crawler_studio_draft_acl_access ON ops_crawler_studio_drafts
    AS PERMISSIVE FOR ALL TO mooncen_crawler_api
    USING (TRUE) WITH CHECK (TRUE);
DROP POLICY IF EXISTS crawler_studio_draft_environment ON ops_crawler_studio_drafts;
CREATE POLICY crawler_studio_draft_environment ON ops_crawler_studio_drafts
    AS RESTRICTIVE FOR ALL
    TO mooncen_crawler_api
    USING (environment = current_crawler_api_environment())
    WITH CHECK (environment = current_crawler_api_environment());
DROP POLICY IF EXISTS crawler_studio_revision_acl_access ON ops_crawler_studio_revisions;
CREATE POLICY crawler_studio_revision_acl_access ON ops_crawler_studio_revisions
    AS PERMISSIVE FOR ALL TO mooncen_crawler_api
    USING (TRUE) WITH CHECK (TRUE);
DROP POLICY IF EXISTS crawler_studio_revision_environment ON ops_crawler_studio_revisions;
CREATE POLICY crawler_studio_revision_environment ON ops_crawler_studio_revisions
    AS RESTRICTIVE FOR ALL
    TO mooncen_crawler_api
    USING (environment = current_crawler_api_environment())
    WITH CHECK (environment = current_crawler_api_environment());
DROP POLICY IF EXISTS crawler_studio_review_acl_access ON ops_crawler_studio_reviews;
CREATE POLICY crawler_studio_review_acl_access ON ops_crawler_studio_reviews
    AS PERMISSIVE FOR ALL TO mooncen_crawler_api
    USING (TRUE) WITH CHECK (TRUE);
DROP POLICY IF EXISTS crawler_studio_review_environment ON ops_crawler_studio_reviews;
CREATE POLICY crawler_studio_review_environment ON ops_crawler_studio_reviews
    AS RESTRICTIVE FOR ALL
    TO mooncen_crawler_api
    USING (environment = current_crawler_api_environment())
    WITH CHECK (environment = current_crawler_api_environment());

REVOKE ALL ON TABLE ops_crawler_studio_provider_paths, ops_crawler_studio_drafts,
    ops_crawler_studio_revisions, ops_crawler_studio_reviews FROM PUBLIC, mooncen_api;
REVOKE ALL ON TABLE ops_crawler_studio_provider_paths, ops_crawler_studio_drafts,
    ops_crawler_studio_revisions, ops_crawler_studio_reviews FROM mooncen_crawler_api;
GRANT SELECT ON ops_crawler_studio_provider_paths TO mooncen_crawler_api;
GRANT SELECT, INSERT ON ops_crawler_studio_drafts TO mooncen_crawler_api;
GRANT UPDATE (status, latest_revision)
    ON ops_crawler_studio_drafts TO mooncen_crawler_api;
GRANT SELECT, INSERT ON ops_crawler_studio_revisions TO mooncen_crawler_api;
GRANT SELECT, INSERT ON ops_crawler_studio_reviews TO mooncen_crawler_api;
-- The established monitoring role is read-only across public relations. Make
-- that grant explicit here so the live ACL is identical before and after the
-- roles convergence pass.
GRANT SELECT ON ops_crawler_studio_provider_paths, ops_crawler_studio_drafts,
    ops_crawler_studio_revisions, ops_crawler_studio_reviews TO mooncen_readonly;

COMMENT ON TABLE ops_crawler_studio_revisions IS
    'Append-only UTF-8 crawler source drafts; storage does not authorize execution, build, signing, or deployment.';

-- Fail closed on post-install catalog drift. Plain CREATE above already
-- rejects every unledgered pre-existing object. The installer and runtime API call this same live
-- catalog verifier, so a valid migration ledger cannot conceal later drift.
CREATE OR REPLACE FUNCTION crawler_studio_contract_is_valid()
RETURNS BOOLEAN
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $crawler_studio_contract$
DECLARE
    actual_columns TEXT[];
    actual_types TEXT[];
    all_columns_not_null BOOLEAN;
    constraint_signature TEXT[];
    relational_constraint_signature TEXT[];
    check_constraint_signature TEXT[];
    default_signature TEXT[];
    trigger_signature TEXT[];
    policy_signature TEXT[];
    allowlist_count INTEGER;
    allowlist_digest TEXT;
    function_contract BOOLEAN;
    acl_contract BOOLEAN;
    table_acl_signature TEXT[];
    column_acl_signature TEXT[];
    function_acl_signature TEXT[];
    index_signature TEXT[];
BEGIN
    IF to_regclass('public.ops_crawler_studio_provider_paths') IS NULL
       OR to_regclass('public.ops_crawler_studio_drafts') IS NULL
       OR to_regclass('public.ops_crawler_studio_revisions') IS NULL
       OR to_regclass('public.ops_crawler_studio_reviews') IS NULL
       OR to_regrole('mooncen_crawler_api') IS NULL
       OR to_regrole('mooncen_api') IS NULL THEN
        RETURN FALSE;
    END IF;
    IF (
        SELECT count(*) <> 3 OR bool_or(
            role_row.rolcanlogin
            OR role_row.rolsuper
            OR role_row.rolcreatedb
            OR role_row.rolcreaterole
            OR role_row.rolreplication
            OR role_row.rolbypassrls
        )
        FROM pg_roles role_row
        WHERE role_row.rolname IN (
            'mooncen_crawler_api', 'mooncen_api', 'mooncen_readonly'
        )
    ) OR EXISTS (
        SELECT 1
        FROM pg_auth_members membership
        JOIN pg_roles member_role ON member_role.oid = membership.member
        WHERE member_role.rolname IN (
            'mooncen_crawler_api', 'mooncen_api', 'mooncen_readonly'
        )
    ) THEN
        RETURN FALSE;
    END IF;

    SELECT count(*),
           encode(public.digest(convert_to(
               string_agg(provider || E'\t' || source_path, E'\n' ORDER BY provider, source_path),
               'UTF8'
           ), 'sha256'), 'hex')
    INTO allowlist_count, allowlist_digest
    FROM public.ops_crawler_studio_provider_paths;
    IF allowlist_count <> 42
       OR allowlist_digest <> '873172712aac8dd01e919864fece65b662f47adf0d4f9f0d404ce4bbebe350f4'
       OR EXISTS (
           SELECT 1
           FROM pg_class table_row
           WHERE table_row.oid IN (
               'public.ops_crawler_studio_provider_paths'::regclass,
               'public.ops_crawler_studio_drafts'::regclass,
               'public.ops_crawler_studio_revisions'::regclass,
               'public.ops_crawler_studio_reviews'::regclass
           ) AND (
               table_row.relkind <> 'r'
               OR table_row.relpersistence <> 'p'
               OR table_row.relispartition
           )
       ) OR EXISTS (
           SELECT 1
           FROM pg_class table_row
           WHERE table_row.oid =
               'public.ops_crawler_studio_provider_paths'::regclass
             AND (table_row.relrowsecurity OR table_row.relforcerowsecurity)
       ) OR (
           SELECT count(DISTINCT table_row.relowner)
           FROM pg_class table_row
           WHERE table_row.oid IN (
               'public.ops_crawler_studio_provider_paths'::regclass,
               'public.ops_crawler_studio_drafts'::regclass,
               'public.ops_crawler_studio_revisions'::regclass,
               'public.ops_crawler_studio_reviews'::regclass
           )
       ) <> 1 OR NOT EXISTS (
           SELECT 1
           FROM pg_class table_row
           JOIN pg_roles owner_row ON owner_row.oid = table_row.relowner
           WHERE table_row.oid =
               'public.ops_crawler_studio_provider_paths'::regclass
             AND table_row.relowner = (
                 SELECT namespace_row.nspowner
                 FROM pg_namespace namespace_row
                 WHERE namespace_row.nspname = 'public'
             )
             AND NOT owner_row.rolcanlogin
             AND NOT owner_row.rolsuper
             AND NOT owner_row.rolcreaterole
             AND NOT owner_row.rolcreatedb
             AND NOT owner_row.rolreplication
             AND NOT owner_row.rolbypassrls
       ) THEN
        RETURN FALSE;
    END IF;
    SELECT array_agg(attribute.attname::text ORDER BY attribute.attnum),
           array_agg(format_type(attribute.atttypid, attribute.atttypmod)
                     ORDER BY attribute.attnum),
           bool_and(attribute.attnotnull)
    INTO actual_columns, actual_types, all_columns_not_null
    FROM pg_attribute attribute
    WHERE attribute.attrelid = 'public.ops_crawler_studio_provider_paths'::regclass
      AND attribute.attnum > 0 AND NOT attribute.attisdropped;
    IF actual_columns <> ARRAY['provider', 'source_path', 'created_at']::TEXT[]
       OR actual_types <> ARRAY['text', 'text', 'timestamp with time zone']::TEXT[]
       OR all_columns_not_null IS NOT TRUE THEN
        RETURN FALSE;
    END IF;

    SELECT array_agg(attribute.attname::text ORDER BY attribute.attnum),
           array_agg(format_type(attribute.atttypid, attribute.atttypmod)
                     ORDER BY attribute.attnum),
           bool_and(attribute.attnotnull)
    INTO actual_columns, actual_types, all_columns_not_null
    FROM pg_attribute attribute
    WHERE attribute.attrelid = 'public.ops_crawler_studio_drafts'::regclass
      AND attribute.attnum > 0 AND NOT attribute.attisdropped;
    IF actual_columns <> ARRAY[
        'id', 'environment', 'provider', 'source_path', 'title', 'status',
        'latest_revision', 'created_by', 'created_login', 'created_at', 'updated_at'
    ]::TEXT[] OR actual_types <> ARRAY[
        'uuid', 'text', 'text', 'text', 'text', 'text', 'integer', 'uuid',
        'name', 'timestamp with time zone', 'timestamp with time zone'
    ]::TEXT[] OR all_columns_not_null IS NOT TRUE THEN
        RETURN FALSE;
    END IF;

    SELECT array_agg(attribute.attname::text ORDER BY attribute.attnum),
           array_agg(format_type(attribute.atttypid, attribute.atttypmod)
                     ORDER BY attribute.attnum),
           bool_and(attribute.attnotnull)
    INTO actual_columns, actual_types, all_columns_not_null
    FROM pg_attribute attribute
    WHERE attribute.attrelid = 'public.ops_crawler_studio_revisions'::regclass
      AND attribute.attnum > 0 AND NOT attribute.attisdropped;
    IF actual_columns <> ARRAY[
        'id', 'draft_id', 'environment', 'revision', 'impacted_providers',
        'source_sha256', 'source_size_bytes', 'source_text', 'change_summary',
        'created_by', 'created_login', 'created_at'
    ]::TEXT[] OR actual_types <> ARRAY[
        'uuid', 'uuid', 'text', 'integer', 'text[]', 'text', 'integer', 'text',
        'text', 'uuid', 'name', 'timestamp with time zone'
    ]::TEXT[] OR all_columns_not_null IS NOT TRUE THEN
        RETURN FALSE;
    END IF;

    SELECT array_agg(attribute.attname::text ORDER BY attribute.attnum),
           array_agg(format_type(attribute.atttypid, attribute.atttypmod)
                     ORDER BY attribute.attnum),
           bool_and(attribute.attnotnull)
    INTO actual_columns, actual_types, all_columns_not_null
    FROM pg_attribute attribute
    WHERE attribute.attrelid = 'public.ops_crawler_studio_reviews'::regclass
      AND attribute.attnum > 0 AND NOT attribute.attisdropped;
    IF actual_columns <> ARRAY[
        'id', 'draft_id', 'environment', 'revision', 'decision', 'comment',
        'reviewed_by', 'reviewer_login', 'created_at'
    ]::TEXT[] OR actual_types <> ARRAY[
        'uuid', 'uuid', 'text', 'integer', 'text', 'text', 'uuid', 'name',
        'timestamp with time zone'
    ]::TEXT[] OR all_columns_not_null IS NOT TRUE THEN
        RETURN FALSE;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_attribute attribute
        WHERE attribute.attrelid IN (
            'public.ops_crawler_studio_provider_paths'::regclass,
            'public.ops_crawler_studio_drafts'::regclass,
            'public.ops_crawler_studio_revisions'::regclass,
            'public.ops_crawler_studio_reviews'::regclass
        ) AND attribute.attnum > 0
          AND NOT attribute.attisdropped
          AND (attribute.attidentity <> '' OR attribute.attgenerated <> '')
    ) THEN
        RETURN FALSE;
    END IF;

    SELECT array_agg(
        table_row.relname::text || ':' || attribute.attname::text || ':' ||
        pg_get_expr(default_row.adbin, default_row.adrelid)
        ORDER BY table_row.relname, attribute.attnum
    ) INTO default_signature
    FROM pg_attrdef default_row
    JOIN pg_attribute attribute
      ON attribute.attrelid = default_row.adrelid
     AND attribute.attnum = default_row.adnum
    JOIN pg_class table_row ON table_row.oid = default_row.adrelid
    WHERE default_row.adrelid IN (
        'public.ops_crawler_studio_provider_paths'::regclass,
        'public.ops_crawler_studio_drafts'::regclass,
        'public.ops_crawler_studio_revisions'::regclass,
        'public.ops_crawler_studio_reviews'::regclass
    );
    IF default_signature <> ARRAY[
        'ops_crawler_studio_drafts:id:gen_random_uuid()',
        'ops_crawler_studio_drafts:status:''draft''::text',
        'ops_crawler_studio_drafts:latest_revision:0',
        'ops_crawler_studio_drafts:created_login:SESSION_USER',
        'ops_crawler_studio_drafts:created_at:clock_timestamp()',
        'ops_crawler_studio_drafts:updated_at:clock_timestamp()',
        'ops_crawler_studio_provider_paths:created_at:clock_timestamp()',
        'ops_crawler_studio_reviews:id:gen_random_uuid()',
        'ops_crawler_studio_reviews:reviewer_login:SESSION_USER',
        'ops_crawler_studio_reviews:created_at:clock_timestamp()',
        'ops_crawler_studio_revisions:id:gen_random_uuid()',
        'ops_crawler_studio_revisions:created_login:SESSION_USER',
        'ops_crawler_studio_revisions:created_at:clock_timestamp()'
    ]::TEXT[] THEN
        RETURN FALSE;
    END IF;

    SELECT array_agg(
        constraint_row.conname::text || ':' || constraint_row.contype::text
        ORDER BY constraint_row.conname
    ) INTO constraint_signature
    FROM pg_constraint constraint_row
    WHERE constraint_row.conrelid IN (
        'public.ops_crawler_studio_provider_paths'::regclass,
        'public.ops_crawler_studio_drafts'::regclass,
        'public.ops_crawler_studio_revisions'::regclass,
        'public.ops_crawler_studio_reviews'::regclass
    ) AND constraint_row.contype IN ('p', 'u', 'f', 'c');
    IF constraint_signature <> ARRAY[
        'chk_ops_crawler_studio_draft_environment:c',
        'chk_ops_crawler_studio_draft_revision:c',
        'chk_ops_crawler_studio_draft_status:c',
        'chk_ops_crawler_studio_draft_title:c',
        'chk_ops_crawler_studio_provider:c',
        'chk_ops_crawler_studio_review_comment:c',
        'chk_ops_crawler_studio_review_decision:c',
        'chk_ops_crawler_studio_review_environment:c',
        'chk_ops_crawler_studio_revision_environment:c',
        'chk_ops_crawler_studio_revision_impacted_providers:c',
        'chk_ops_crawler_studio_revision_number:c',
        'chk_ops_crawler_studio_revision_sha:c',
        'chk_ops_crawler_studio_revision_size:c',
        'chk_ops_crawler_studio_revision_source:c',
        'chk_ops_crawler_studio_revision_summary:c',
        'chk_ops_crawler_studio_source_path:c',
        'fk_ops_crawler_studio_draft_api_binding:f',
        'fk_ops_crawler_studio_draft_path:f',
        'fk_ops_crawler_studio_review_api_binding:f',
        'fk_ops_crawler_studio_review_revision:f',
        'fk_ops_crawler_studio_revision_api_binding:f',
        'ops_crawler_studio_drafts_pkey:p',
        'ops_crawler_studio_reviews_draft_id_fkey:f',
        'ops_crawler_studio_reviews_pkey:p',
        'ops_crawler_studio_revisions_draft_id_fkey:f',
        'ops_crawler_studio_revisions_pkey:p',
        'pk_ops_crawler_studio_provider_paths:p',
        'ux_ops_crawler_studio_draft_path:u',
        'ux_ops_crawler_studio_revision:u'
    ]::TEXT[] OR EXISTS (
        SELECT 1 FROM pg_constraint constraint_row
        WHERE constraint_row.conrelid IN (
            'public.ops_crawler_studio_provider_paths'::regclass,
            'public.ops_crawler_studio_drafts'::regclass,
            'public.ops_crawler_studio_revisions'::regclass,
            'public.ops_crawler_studio_reviews'::regclass
        ) AND NOT constraint_row.convalidated
    ) OR EXISTS (
        SELECT 1
        FROM pg_constraint constraint_row
        WHERE constraint_row.conrelid IN (
            'public.ops_crawler_studio_provider_paths'::regclass,
            'public.ops_crawler_studio_drafts'::regclass,
            'public.ops_crawler_studio_revisions'::regclass,
            'public.ops_crawler_studio_reviews'::regclass
        ) AND constraint_row.contype IN ('p', 'u', 'f', 'c')
          AND obj_description(constraint_row.oid, 'pg_constraint') IS DISTINCT FROM
              'mooncen-crawler-studio-constraint-v1:sha256:' ||
              encode(public.digest(convert_to(
                  pg_get_constraintdef(constraint_row.oid), 'UTF8'
              ), 'sha256'), 'hex')
    ) THEN
        RETURN FALSE;
    END IF;

    SELECT array_agg(
        table_row.relname::text || ':' || constraint_row.conname::text || ':' ||
        pg_get_constraintdef(constraint_row.oid)
        ORDER BY table_row.relname, constraint_row.conname
    ) INTO relational_constraint_signature
    FROM pg_constraint constraint_row
    JOIN pg_class table_row ON table_row.oid = constraint_row.conrelid
    WHERE constraint_row.conrelid IN (
        'public.ops_crawler_studio_provider_paths'::regclass,
        'public.ops_crawler_studio_drafts'::regclass,
        'public.ops_crawler_studio_revisions'::regclass,
        'public.ops_crawler_studio_reviews'::regclass
    ) AND constraint_row.contype IN ('p', 'u', 'f');
    IF relational_constraint_signature <> ARRAY[
        'ops_crawler_studio_drafts:fk_ops_crawler_studio_draft_api_binding:' ||
            'FOREIGN KEY (created_login, environment) REFERENCES ' ||
            'ops_crawler_api_bindings(database_login, environment) ' ||
            'ON UPDATE RESTRICT ON DELETE RESTRICT',
        'ops_crawler_studio_drafts:fk_ops_crawler_studio_draft_path:' ||
            'FOREIGN KEY (provider, source_path) REFERENCES ' ||
            'ops_crawler_studio_provider_paths(provider, source_path) ' ||
            'ON UPDATE RESTRICT ON DELETE RESTRICT',
        'ops_crawler_studio_drafts:ops_crawler_studio_drafts_pkey:PRIMARY KEY (id)',
        'ops_crawler_studio_drafts:ux_ops_crawler_studio_draft_path:' ||
            'UNIQUE (environment, source_path)',
        'ops_crawler_studio_provider_paths:pk_ops_crawler_studio_provider_paths:' ||
            'PRIMARY KEY (provider, source_path)',
        'ops_crawler_studio_reviews:fk_ops_crawler_studio_review_api_binding:' ||
            'FOREIGN KEY (reviewer_login, environment) REFERENCES ' ||
            'ops_crawler_api_bindings(database_login, environment) ' ||
            'ON UPDATE RESTRICT ON DELETE RESTRICT',
        'ops_crawler_studio_reviews:fk_ops_crawler_studio_review_revision:' ||
            'FOREIGN KEY (draft_id, revision) REFERENCES ' ||
            'ops_crawler_studio_revisions(draft_id, revision) ' ||
            'ON UPDATE RESTRICT ON DELETE RESTRICT',
        'ops_crawler_studio_reviews:ops_crawler_studio_reviews_draft_id_fkey:' ||
            'FOREIGN KEY (draft_id) REFERENCES ops_crawler_studio_drafts(id) ' ||
            'ON DELETE RESTRICT',
        'ops_crawler_studio_reviews:ops_crawler_studio_reviews_pkey:PRIMARY KEY (id)',
        'ops_crawler_studio_revisions:fk_ops_crawler_studio_revision_api_binding:' ||
            'FOREIGN KEY (created_login, environment) REFERENCES ' ||
            'ops_crawler_api_bindings(database_login, environment) ' ||
            'ON UPDATE RESTRICT ON DELETE RESTRICT',
        'ops_crawler_studio_revisions:ops_crawler_studio_revisions_draft_id_fkey:' ||
            'FOREIGN KEY (draft_id) REFERENCES ops_crawler_studio_drafts(id) ' ||
            'ON DELETE RESTRICT',
        'ops_crawler_studio_revisions:ops_crawler_studio_revisions_pkey:PRIMARY KEY (id)',
        'ops_crawler_studio_revisions:ux_ops_crawler_studio_revision:' ||
            'UNIQUE (draft_id, revision)'
    ]::TEXT[] THEN
        RETURN FALSE;
    END IF;

    SELECT array_agg(
        table_row.relname::text || ':' || constraint_row.conname::text || ':' ||
        encode(public.digest(convert_to(
            pg_get_constraintdef(constraint_row.oid), 'UTF8'
        ), 'sha256'), 'hex')
        ORDER BY table_row.relname, constraint_row.conname
    ) INTO check_constraint_signature
    FROM pg_constraint constraint_row
    JOIN pg_class table_row ON table_row.oid = constraint_row.conrelid
    WHERE constraint_row.conrelid IN (
        'public.ops_crawler_studio_provider_paths'::regclass,
        'public.ops_crawler_studio_drafts'::regclass,
        'public.ops_crawler_studio_revisions'::regclass,
        'public.ops_crawler_studio_reviews'::regclass
    ) AND constraint_row.contype = 'c';
    IF check_constraint_signature <> ARRAY[
        -- Reviewed canonical pg_get_constraintdef SHA-256 values. These are
        -- independent of live COMMENT metadata and fail closed on same-name
        -- CHECK replacement by an object owner.
        'ops_crawler_studio_drafts:chk_ops_crawler_studio_draft_environment:' ||
            'c1e72420ccaa0e6d9ee7cdb24ec0fb1ba6a1e484341655a992b220f95deaefe7',
        'ops_crawler_studio_drafts:chk_ops_crawler_studio_draft_revision:' ||
            'f0fad53de4dc8904b25b1f345f2ed088614695b149489b6999f4a451c1b4ee95',
        'ops_crawler_studio_drafts:chk_ops_crawler_studio_draft_status:' ||
            'e58a92256b5240720f7213273bc7e9370a524e38541b6708759489e3447e0ddd',
        'ops_crawler_studio_drafts:chk_ops_crawler_studio_draft_title:' ||
            '3f9487f43f38869cfbe9e9f2037ddcde06dcea3f3efb4aeedb46981b3acf44ec',
        'ops_crawler_studio_provider_paths:chk_ops_crawler_studio_provider:' ||
            '7c3b7ca3235ff0f82260e7b6a855db439d58180d47f20e41995c39ba01503a7d',
        'ops_crawler_studio_provider_paths:chk_ops_crawler_studio_source_path:' ||
            'e86cea5808864178c792078b7e343ec033983eec19ca5d91f0ad2fb341551cbb',
        'ops_crawler_studio_reviews:chk_ops_crawler_studio_review_comment:' ||
            'a5ecf7abd5d2b5790148e9d268ef9d1728726f9abc733de3b3fe4e46cc1d87ad',
        'ops_crawler_studio_reviews:chk_ops_crawler_studio_review_decision:' ||
            '1b35a59983f9cee634922c64c68aced62df1e1f9c70cf90fa60be1bef896503f',
        'ops_crawler_studio_reviews:chk_ops_crawler_studio_review_environment:' ||
            'c1e72420ccaa0e6d9ee7cdb24ec0fb1ba6a1e484341655a992b220f95deaefe7',
        'ops_crawler_studio_revisions:chk_ops_crawler_studio_revision_environment:' ||
            'c1e72420ccaa0e6d9ee7cdb24ec0fb1ba6a1e484341655a992b220f95deaefe7',
        'ops_crawler_studio_revisions:chk_ops_crawler_studio_revision_impacted_providers:' ||
            'f0445a6f8ecb86df3aa2bb27262ddb4139f71961a28542593de17d9347d66dc2',
        'ops_crawler_studio_revisions:chk_ops_crawler_studio_revision_number:' ||
            '7c0ddfd91f9bdc7bd607f558b7220a0d09efdbea0973a07402460329265b9c9f',
        'ops_crawler_studio_revisions:chk_ops_crawler_studio_revision_sha:' ||
            'fe4188a3ae6d57a4927cc8e848d2fbee75711010f410480b2594f2abc8a8195f',
        'ops_crawler_studio_revisions:chk_ops_crawler_studio_revision_size:' ||
            '01c78e93751f07b08f90978f4bee4512c6776e33a93d051adc90d2ce896b13d0',
        'ops_crawler_studio_revisions:chk_ops_crawler_studio_revision_source:' ||
            '366be37d9343e972ccd73dca9c4b8fe246232b1e64ea43901e8f414aa4e29db9',
        'ops_crawler_studio_revisions:chk_ops_crawler_studio_revision_summary:' ||
            '2d64a7408e66d9a33024f74b87db143fc22590eaaf60dc0810ee270daec1a51c'
    ]::TEXT[] THEN
        RETURN FALSE;
    END IF;

    SELECT array_agg(
        table_row.relname::text || ':' || index_class.relname::text || ':' ||
        index_row.indisprimary::text || ':' || index_row.indisunique::text || ':' ||
        (
            SELECT string_agg(
                attribute.attname || CASE
                    WHEN (index_row.indoption[position] & 1) = 1 THEN ' DESC'
                    ELSE ' ASC'
                END,
                ',' ORDER BY position
            )
            FROM generate_subscripts(index_row.indkey, 1) position
            JOIN pg_attribute attribute
              ON attribute.attrelid = index_row.indrelid
             AND attribute.attnum = index_row.indkey[position]
        )
        ORDER BY table_row.relname, index_class.relname
    ) INTO index_signature
    FROM pg_index index_row
    JOIN pg_class index_class ON index_class.oid = index_row.indexrelid
    JOIN pg_class table_row ON table_row.oid = index_row.indrelid
    JOIN pg_am access_method ON access_method.oid = index_class.relam
    WHERE table_row.oid IN (
        'public.ops_crawler_studio_provider_paths'::regclass,
        'public.ops_crawler_studio_drafts'::regclass,
        'public.ops_crawler_studio_revisions'::regclass,
        'public.ops_crawler_studio_reviews'::regclass
    ) AND index_row.indisvalid
      AND index_row.indisready
      AND index_row.indislive
      AND index_row.indimmediate
      AND NOT index_row.indisexclusion
      AND index_row.indexprs IS NULL
      AND index_row.indpred IS NULL
      AND index_row.indnkeyatts = index_row.indnatts
      AND access_method.amname = 'btree';
    IF index_signature <> ARRAY[
        'ops_crawler_studio_drafts:idx_ops_crawler_studio_drafts_environment:' ||
            'false:false:environment ASC,updated_at DESC,id DESC',
        'ops_crawler_studio_drafts:ops_crawler_studio_drafts_pkey:' ||
            'true:true:id ASC',
        'ops_crawler_studio_drafts:ux_ops_crawler_studio_draft_path:' ||
            'false:true:environment ASC,source_path ASC',
        'ops_crawler_studio_provider_paths:pk_ops_crawler_studio_provider_paths:' ||
            'true:true:provider ASC,source_path ASC',
        'ops_crawler_studio_reviews:idx_ops_crawler_studio_reviews_draft:' ||
            'false:false:draft_id ASC,created_at DESC,id DESC',
        'ops_crawler_studio_reviews:ops_crawler_studio_reviews_pkey:' ||
            'true:true:id ASC',
        'ops_crawler_studio_revisions:idx_ops_crawler_studio_revisions_draft:' ||
            'false:false:draft_id ASC,revision DESC',
        'ops_crawler_studio_revisions:ops_crawler_studio_revisions_pkey:' ||
            'true:true:id ASC',
        'ops_crawler_studio_revisions:ux_ops_crawler_studio_revision:' ||
            'false:true:draft_id ASC,revision ASC'
    ]::TEXT[] THEN
        RETURN FALSE;
    END IF;

    IF (
        SELECT count(*) <> 2 OR bool_or(
            NOT index_row.indisunique
            OR NOT index_row.indisvalid
            OR NOT index_row.indisready
            OR NOT index_row.indislive
            OR NOT index_row.indimmediate
            OR index_row.indisexclusion
            OR index_row.indexprs IS NOT NULL
            OR index_row.indpred IS NOT NULL
            OR access_method.amname <> 'btree'
        )
        FROM pg_constraint constraint_row
        JOIN pg_index index_row ON index_row.indexrelid = constraint_row.conindid
        JOIN pg_class index_class ON index_class.oid = index_row.indexrelid
        JOIN pg_am access_method ON access_method.oid = index_class.relam
        WHERE constraint_row.conrelid IN (
            'public.ops_crawler_studio_drafts'::regclass,
            'public.ops_crawler_studio_revisions'::regclass
        ) AND constraint_row.conname IN (
            'ux_ops_crawler_studio_draft_path',
            'ux_ops_crawler_studio_revision'
        )
    ) THEN
        RETURN FALSE;
    END IF;

    WITH expected(signature, source_sha256) AS (
        VALUES
            ('enforce_crawler_studio_append_only()',
             'c3b1b59018d5a1a915a72a8ef7737079fc2bdbe44a951d01c981bb3ddce3cda6'),
            ('enforce_crawler_studio_draft_transition()',
             'aa89de0dfe58237d09955f4911ca222335449d2fd9b5f22ef0fb2a73c6690fd0'),
            ('enforce_crawler_studio_revision_insert()',
             'dae181ba946b98c034e20c26d06c167b122d3372ab5a553a556b4a3c3e9c1758'),
            ('enforce_crawler_studio_review_insert()',
             '78575561c02c4ae46ec0ff414af96712455b33109f991af1ebce8e0e55d811cd'),
            ('enforce_crawler_studio_draft_commit()',
             '41bc7a5bf5392dee1cef5b2f01689a49a9a56f0828101067d24b7ba3d6e58ca6'),
            ('enforce_crawler_studio_revision_commit()',
             '1b92a13bf9cdcbdf40871a3ee1c2fcec1bff2223a369b37cfc8fd78b926c8237'),
            ('enforce_crawler_studio_review_commit()',
             'a176842fadc466030e4f5edb3166bf4c413fca650925c124db9801825d9b5ea4')
    ), actual AS (
        SELECT expected.signature,
               procedure.oid,
               procedure.proowner,
               encode(public.digest(convert_to(
                   replace(replace(
                       procedure.prosrc,
                       chr(13) || chr(10), chr(10)
                   ), chr(13), chr(10)),
                   'UTF8'
               ), 'sha256'), 'hex')
                   = expected.source_sha256
               AND language.lanname = 'plpgsql'
               AND procedure.prokind = 'f'
               AND procedure.pronargs = 0
               AND procedure.prorettype = 'trigger'::regtype
               AND procedure.provolatile = 'v'
               AND procedure.proparallel = 'u'
               AND NOT procedure.prosecdef
               AND NOT procedure.proleakproof
               AND procedure.proconfig = ARRAY['search_path=pg_catalog, public']::TEXT[]
               AND procedure.proowner = (
                   SELECT owned_table.relowner
                   FROM pg_class owned_table
                   WHERE owned_table.oid =
                       'public.ops_crawler_studio_provider_paths'::regclass
               )
               AND NOT EXISTS (
                   SELECT 1
                   FROM aclexplode(COALESCE(
                       procedure.proacl,
                       acldefault('f', procedure.proowner)
                   )) function_acl
                   WHERE function_acl.grantee <> procedure.proowner
               )
                   AS valid
        FROM expected
        LEFT JOIN pg_proc procedure
          ON procedure.oid = to_regprocedure('public.' || expected.signature)
        LEFT JOIN pg_language language ON language.oid = procedure.prolang
    )
    SELECT count(*) = 7
           AND bool_and(actual.oid IS NOT NULL AND actual.valid)
           AND count(DISTINCT actual.proowner) = 1
           AND NOT EXISTS (
               SELECT 1
               FROM pg_proc unexpected
               JOIN pg_namespace namespace_row ON namespace_row.oid = unexpected.pronamespace
               WHERE namespace_row.nspname = 'public'
                 AND unexpected.proname LIKE 'enforce_crawler_studio_%'
                 AND unexpected.oid <> ALL (
                     SELECT checked.oid FROM actual checked WHERE checked.oid IS NOT NULL
                 )
           )
    INTO function_contract
    FROM actual;
    IF function_contract IS NOT TRUE THEN
        RETURN FALSE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint constraint_row
        WHERE constraint_row.conrelid = 'public.ops_crawler_studio_drafts'::regclass
          AND constraint_row.conname = 'ux_ops_crawler_studio_draft_path'
          AND pg_get_constraintdef(constraint_row.oid) = 'UNIQUE (environment, source_path)'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint constraint_row
        WHERE constraint_row.conrelid = 'public.ops_crawler_studio_drafts'::regclass
          AND constraint_row.conname = 'fk_ops_crawler_studio_draft_path'
          AND constraint_row.confrelid = 'public.ops_crawler_studio_provider_paths'::regclass
          AND pg_get_constraintdef(constraint_row.oid) LIKE
              'FOREIGN KEY (provider, source_path) REFERENCES %'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint constraint_row
        WHERE constraint_row.conrelid = 'public.ops_crawler_studio_revisions'::regclass
          AND constraint_row.conname = 'ux_ops_crawler_studio_revision'
          AND pg_get_constraintdef(constraint_row.oid) = 'UNIQUE (draft_id, revision)'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint constraint_row
        WHERE constraint_row.conrelid = 'public.ops_crawler_studio_reviews'::regclass
          AND constraint_row.conname = 'fk_ops_crawler_studio_review_revision'
          AND constraint_row.confrelid = 'public.ops_crawler_studio_revisions'::regclass
          AND pg_get_constraintdef(constraint_row.oid) LIKE
              'FOREIGN KEY (draft_id, revision) REFERENCES %'
    ) THEN
        RETURN FALSE;
    END IF;

    SELECT procedure.prokind = 'f'
           AND procedure.pronargs = 0
           AND procedure.prorettype = 'boolean'::regtype
           AND procedure.provolatile = 's'
           AND procedure.proparallel = 'u'
           AND NOT procedure.prosecdef
           AND NOT procedure.proleakproof
           AND language.lanname = 'plpgsql'
           AND procedure.proconfig = ARRAY['search_path=pg_catalog, public']::TEXT[]
           AND procedure.proowner = table_row.relowner
    INTO acl_contract
    FROM pg_proc procedure
    JOIN pg_language language ON language.oid = procedure.prolang
    JOIN pg_class table_row
      ON table_row.oid = 'public.ops_crawler_studio_provider_paths'::regclass
    WHERE procedure.oid =
        to_regprocedure('public.crawler_studio_contract_is_valid()');
    IF acl_contract IS NOT TRUE THEN
        RETURN FALSE;
    END IF;

    SELECT array_agg(
        COALESCE(role_row.rolname, '<public>') || ':' ||
        function_acl.privilege_type || ':' || function_acl.is_grantable::text
        ORDER BY COALESCE(role_row.rolname, '<public>'), function_acl.privilege_type
    ) INTO function_acl_signature
    FROM pg_proc procedure
    CROSS JOIN LATERAL aclexplode(COALESCE(
        procedure.proacl,
        acldefault('f', procedure.proowner)
    )) function_acl
    LEFT JOIN pg_roles role_row ON role_row.oid = function_acl.grantee
    WHERE procedure.oid =
        to_regprocedure('public.crawler_studio_contract_is_valid()')
      AND function_acl.grantee <> procedure.proowner;
    IF function_acl_signature <> ARRAY[
        'mooncen_crawler_api:EXECUTE:false'
    ]::TEXT[] THEN
        RETURN FALSE;
    END IF;

    SELECT array_agg(
        table_row.relname::text || ':' || trigger_row.tgname::text || ':' ||
        procedure.proname::text || ':' || trigger_row.tgtype::text || ':' ||
        trigger_row.tgenabled::text || ':' ||
        COALESCE(constraint_row.condeferrable::text, 'false') || ':' ||
        COALESCE(constraint_row.condeferred::text, 'false')
        ORDER BY table_row.relname, trigger_row.tgname
    ) INTO trigger_signature
    FROM pg_trigger trigger_row
    JOIN pg_class table_row ON table_row.oid = trigger_row.tgrelid
    JOIN pg_proc procedure ON procedure.oid = trigger_row.tgfoid
    LEFT JOIN pg_constraint constraint_row ON constraint_row.oid = trigger_row.tgconstraint
    WHERE trigger_row.tgrelid IN (
        'public.ops_crawler_studio_provider_paths'::regclass,
        'public.ops_crawler_studio_drafts'::regclass,
        'public.ops_crawler_studio_revisions'::regclass,
        'public.ops_crawler_studio_reviews'::regclass
    ) AND NOT trigger_row.tgisinternal;
    IF trigger_signature <> ARRAY[
        'ops_crawler_studio_drafts:zz_ops_crawler_studio_draft_commit:' ||
            'enforce_crawler_studio_draft_commit:5:O:true:true',
        'ops_crawler_studio_drafts:zz_ops_crawler_studio_draft_transition:' ||
            'enforce_crawler_studio_draft_transition:31:O:false:false',
        'ops_crawler_studio_provider_paths:zz_ops_crawler_studio_paths_immutable:' ||
            'enforce_crawler_studio_append_only:27:O:false:false',
        'ops_crawler_studio_reviews:zz_ops_crawler_studio_review_commit:' ||
            'enforce_crawler_studio_review_commit:5:O:true:true',
        'ops_crawler_studio_reviews:zz_ops_crawler_studio_review_insert:' ||
            'enforce_crawler_studio_review_insert:7:O:false:false',
        'ops_crawler_studio_reviews:zz_ops_crawler_studio_reviews_immutable:' ||
            'enforce_crawler_studio_append_only:27:O:false:false',
        'ops_crawler_studio_revisions:zz_ops_crawler_studio_revision_commit:' ||
            'enforce_crawler_studio_revision_commit:5:O:true:true',
        'ops_crawler_studio_revisions:zz_ops_crawler_studio_revision_insert:' ||
            'enforce_crawler_studio_revision_insert:7:O:false:false',
        'ops_crawler_studio_revisions:zz_ops_crawler_studio_revisions_immutable:' ||
            'enforce_crawler_studio_append_only:27:O:false:false'
    ]::TEXT[] THEN
        RETURN FALSE;
    END IF;

    SELECT array_agg(
        table_row.relname::text || ':' || policy.polname::text || ':' ||
        policy.polpermissive::text || ':' || policy.polcmd::text || ':' ||
        (
            SELECT string_agg(role_row.rolname, ',' ORDER BY role_row.rolname)
            FROM unnest(policy.polroles) policy_role(role_oid)
            JOIN pg_roles role_row ON role_row.oid = policy_role.role_oid
        ) || ':' || COALESCE(pg_get_expr(policy.polqual, policy.polrelid), '<null>') ||
        ':' || COALESCE(pg_get_expr(policy.polwithcheck, policy.polrelid), '<null>')
        ORDER BY table_row.relname, policy.polname
    ) INTO policy_signature
    FROM pg_policy policy
    JOIN pg_class table_row ON table_row.oid = policy.polrelid
    WHERE policy.polrelid IN (
        'public.ops_crawler_studio_drafts'::regclass,
        'public.ops_crawler_studio_revisions'::regclass,
        'public.ops_crawler_studio_reviews'::regclass
    );
    IF policy_signature <> ARRAY[
        'ops_crawler_studio_drafts:crawler_studio_draft_acl_access:' ||
            'true:*:mooncen_crawler_api:true:true',
        'ops_crawler_studio_drafts:crawler_studio_draft_environment:' ||
            'false:*:mooncen_crawler_api:(environment = current_crawler_api_environment()):' ||
            '(environment = current_crawler_api_environment())',
        'ops_crawler_studio_reviews:crawler_studio_review_acl_access:' ||
            'true:*:mooncen_crawler_api:true:true',
        'ops_crawler_studio_reviews:crawler_studio_review_environment:' ||
            'false:*:mooncen_crawler_api:(environment = current_crawler_api_environment()):' ||
            '(environment = current_crawler_api_environment())',
        'ops_crawler_studio_revisions:crawler_studio_revision_acl_access:' ||
            'true:*:mooncen_crawler_api:true:true',
        'ops_crawler_studio_revisions:crawler_studio_revision_environment:' ||
            'false:*:mooncen_crawler_api:(environment = current_crawler_api_environment()):' ||
            '(environment = current_crawler_api_environment())'
    ]::TEXT[] OR EXISTS (
        SELECT 1 FROM pg_class table_row
        WHERE table_row.oid IN (
            'public.ops_crawler_studio_drafts'::regclass,
            'public.ops_crawler_studio_revisions'::regclass,
            'public.ops_crawler_studio_reviews'::regclass
        ) AND (NOT table_row.relrowsecurity OR NOT table_row.relforcerowsecurity)
    ) THEN
        RETURN FALSE;
    END IF;

    SELECT array_agg(
        table_row.relname::text || ':' ||
        COALESCE(role_row.rolname, '<public>') || ':' ||
        table_acl.privilege_type || ':' || table_acl.is_grantable::text
        ORDER BY table_row.relname, COALESCE(role_row.rolname, '<public>'),
                 table_acl.privilege_type
    ) INTO table_acl_signature
    FROM pg_class table_row
    CROSS JOIN LATERAL aclexplode(COALESCE(
        table_row.relacl,
        acldefault('r', table_row.relowner)
    )) table_acl
    LEFT JOIN pg_roles role_row ON role_row.oid = table_acl.grantee
    WHERE table_row.oid IN (
        'public.ops_crawler_studio_provider_paths'::regclass,
        'public.ops_crawler_studio_drafts'::regclass,
        'public.ops_crawler_studio_revisions'::regclass,
        'public.ops_crawler_studio_reviews'::regclass
    ) AND table_acl.grantee <> table_row.relowner;
    IF table_acl_signature <> ARRAY[
        'ops_crawler_studio_drafts:mooncen_crawler_api:INSERT:false',
        'ops_crawler_studio_drafts:mooncen_crawler_api:SELECT:false',
        'ops_crawler_studio_drafts:mooncen_readonly:SELECT:false',
        'ops_crawler_studio_provider_paths:mooncen_crawler_api:SELECT:false',
        'ops_crawler_studio_provider_paths:mooncen_readonly:SELECT:false',
        'ops_crawler_studio_reviews:mooncen_crawler_api:INSERT:false',
        'ops_crawler_studio_reviews:mooncen_crawler_api:SELECT:false',
        'ops_crawler_studio_reviews:mooncen_readonly:SELECT:false',
        'ops_crawler_studio_revisions:mooncen_crawler_api:INSERT:false',
        'ops_crawler_studio_revisions:mooncen_crawler_api:SELECT:false',
        'ops_crawler_studio_revisions:mooncen_readonly:SELECT:false'
    ]::TEXT[] THEN
        RETURN FALSE;
    END IF;

    SELECT array_agg(
        table_row.relname::text || ':' || attribute.attname::text || ':' ||
        COALESCE(role_row.rolname, '<public>') || ':' ||
        column_acl.privilege_type || ':' || column_acl.is_grantable::text
        ORDER BY table_row.relname, attribute.attname,
                 COALESCE(role_row.rolname, '<public>'), column_acl.privilege_type
    ) INTO column_acl_signature
    FROM pg_class table_row
    JOIN pg_attribute attribute ON attribute.attrelid = table_row.oid
    CROSS JOIN LATERAL aclexplode(attribute.attacl) column_acl
    LEFT JOIN pg_roles role_row ON role_row.oid = column_acl.grantee
    WHERE table_row.oid IN (
        'public.ops_crawler_studio_provider_paths'::regclass,
        'public.ops_crawler_studio_drafts'::regclass,
        'public.ops_crawler_studio_revisions'::regclass,
        'public.ops_crawler_studio_reviews'::regclass
    ) AND attribute.attnum > 0
      AND NOT attribute.attisdropped
      AND column_acl.grantee <> table_row.relowner;
    IF column_acl_signature <> ARRAY[
        'ops_crawler_studio_drafts:latest_revision:mooncen_crawler_api:UPDATE:false',
        'ops_crawler_studio_drafts:status:mooncen_crawler_api:UPDATE:false'
    ]::TEXT[] THEN
        RETURN FALSE;
    END IF;

    IF NOT has_table_privilege('mooncen_crawler_api', 'ops_crawler_studio_provider_paths', 'SELECT')
       OR has_table_privilege('mooncen_crawler_api', 'ops_crawler_studio_provider_paths', 'INSERT')
       OR has_table_privilege('mooncen_crawler_api', 'ops_crawler_studio_provider_paths', 'UPDATE')
       OR has_table_privilege('mooncen_crawler_api', 'ops_crawler_studio_provider_paths', 'DELETE')
       OR NOT has_table_privilege('mooncen_crawler_api', 'ops_crawler_studio_drafts', 'SELECT')
       OR NOT has_table_privilege('mooncen_crawler_api', 'ops_crawler_studio_drafts', 'INSERT')
       OR has_table_privilege('mooncen_crawler_api', 'ops_crawler_studio_drafts', 'UPDATE')
       OR has_table_privilege('mooncen_crawler_api', 'ops_crawler_studio_drafts', 'DELETE')
       OR NOT has_column_privilege('mooncen_crawler_api', 'ops_crawler_studio_drafts', 'status', 'UPDATE')
       OR NOT has_column_privilege('mooncen_crawler_api', 'ops_crawler_studio_drafts', 'latest_revision', 'UPDATE')
       OR has_column_privilege('mooncen_crawler_api', 'ops_crawler_studio_drafts', 'title', 'UPDATE')
       OR NOT has_table_privilege('mooncen_crawler_api', 'ops_crawler_studio_revisions', 'SELECT')
       OR NOT has_table_privilege('mooncen_crawler_api', 'ops_crawler_studio_revisions', 'INSERT')
       OR has_table_privilege('mooncen_crawler_api', 'ops_crawler_studio_revisions', 'UPDATE')
       OR has_table_privilege('mooncen_crawler_api', 'ops_crawler_studio_revisions', 'DELETE')
       OR NOT has_table_privilege('mooncen_crawler_api', 'ops_crawler_studio_reviews', 'SELECT')
       OR NOT has_table_privilege('mooncen_crawler_api', 'ops_crawler_studio_reviews', 'INSERT')
       OR has_table_privilege('mooncen_crawler_api', 'ops_crawler_studio_reviews', 'UPDATE')
       OR has_table_privilege('mooncen_crawler_api', 'ops_crawler_studio_reviews', 'DELETE')
       OR has_table_privilege('mooncen_api', 'ops_crawler_studio_drafts', 'SELECT')
       OR has_table_privilege('mooncen_api', 'ops_crawler_studio_drafts', 'INSERT')
       OR has_table_privilege('mooncen_api', 'ops_crawler_studio_drafts', 'UPDATE')
       OR has_table_privilege('mooncen_api', 'ops_crawler_studio_drafts', 'DELETE') THEN
        RETURN FALSE;
    END IF;
    RETURN TRUE;
EXCEPTION WHEN OTHERS THEN
    RETURN FALSE;
END;
$crawler_studio_contract$;

REVOKE ALL ON FUNCTION crawler_studio_contract_is_valid() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION crawler_studio_contract_is_valid()
    TO mooncen_crawler_api;

DO $$
BEGIN
    IF public.crawler_studio_contract_is_valid() IS NOT TRUE THEN
        RAISE EXCEPTION 'crawler studio live catalog contract differs';
    END IF;
END;
$$;
