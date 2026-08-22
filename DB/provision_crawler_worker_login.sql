-- DEPRECATED — DO NOT USE.
--
-- Distributed worker and reporter logins require client-generated SCRAM
-- verifiers plus an atomic server-side ops_agent/login binding.  This legacy
-- psql entry point cannot satisfy that contract and intentionally fails before
-- reading variables or changing PostgreSQL state.
--
-- Use deploy/ubuntu/enroll_distributed_crawler_worker.sh, which invokes
-- tools/provision_crawler_service_login.py for the confirmed shared staging
-- control database.

\set ON_ERROR_STOP on
\echo 'DB/provision_crawler_worker_login.sql is disabled; use enroll_distributed_crawler_worker.sh'
\quit 3
