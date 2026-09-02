-- Keep the host being observed separate from the Agent that reported it.
-- Agent hostname is provenance; it is not necessarily the service location.

ALTER TABLE ops_services
    ADD COLUMN service_host TEXT;

ALTER TABLE ops_services
    ADD CONSTRAINT chk_ops_services_service_host
    CHECK (
        service_host IS NULL
        OR service_host ~ '^[A-Za-z0-9:][A-Za-z0-9._:-]{0,252}$'
    );

COMMENT ON COLUMN ops_services.service_host IS
    'Hostname of the checked service endpoint; distinct from ops_agents.hostname (reporter).';

-- Preserve useful endpoint locations for rows written before this migration.
UPDATE ops_services
SET service_host = substring(
    health_url FROM '^[A-Za-z][A-Za-z0-9+.-]*://([^/:?#]+)'
)
WHERE service_host IS NULL
  AND health_url ~ '^[A-Za-z][A-Za-z0-9+.-]*://[^/:?#]+';
