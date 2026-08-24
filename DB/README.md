# MoonCen DB Setup

MoonCen uses PostgreSQL with PostGIS.

## Fresh database

Use this when the database is empty:

```bash
python DB/setup_db.py --mode fresh
```

## Existing database

Use this to upgrade an existing database to the schema expected by the current
FastAPI backend, crawlers, and AI worker:

```bash
python DB/setup_db.py --mode migrate
```

The migration is idempotent and can be run repeatedly.

`setup_db.py` also applies immutable files in `DB/migrations/` once and records
their SHA-256 checksums in `mooncen_schema_migrations`. A changed applied file
stops setup; corrections must use a new migration version. The service-group SQL is generated from the
authoritative constants in `service_group.py`; tests reject manual drift.
Each migration file and its ledger row commit atomically. A session advisory
lock serializes the whole batch, while a failure rolls back only the current
file so already completed migrations are not needlessly repeated.

## Connection settings

Database credentials are loaded from the project root `.env`:

```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=mooncen
DB_USER=mooncen_admin
DB_PASSWORD=your_secure_password_here
```

## Verification

```bash
python DB/db_status.py
```

The current application schema includes:

- `branches.lat` / `branches.lon` for API responses and map rendering
- `branches.location` synced from `lat/lon` for PostGIS indexing
- normalized schedule fields such as `schedule_days` and `schedule_time_start`
- normalized target fields such as `target_age_group`
- AI fields such as `ai_category`, `ai_tags`, and `ai_summary`

## Staging apply objects

- `staging_schema.sql` is staging-database only.
- `staging_primary_schema.sql` contains only primary-side apply logs/snapshots.
- Batch apply code must never execute `staging_schema.sql` on primary.

## Runtime roles and legacy NULL cleanup

Run `roles.sql` explicitly as a cluster role administrator, then give each
service a separate LOGIN role as described in `ROLE_SEPARATION.md`.

The fee/material-fee/schedule default transition and deferred constraint
validation process are documented in `UNKNOWN_NULL_TRANSITION.md`.

## Safe close-missing guardrails

The staging applier closes missing courses only when the crawl recorded an
explicitly complete, unfiltered provider run and the staged provider counts
match that evidence. It also blocks a provider whose incoming count falls too
far below its current active-course baseline.

Optional environment overrides:

```env
# Smallest accepted incoming/current ratio once the baseline is reached.
STAGING_CLOSE_MIN_RATIO=0.65
# Largest accepted absolute active-course drop per provider.
STAGING_CLOSE_MAX_ABSOLUTE_DROP=2000
# Do not apply the ratio check below this current active-course count.
STAGING_CLOSE_RATIO_BASELINE=20
```

A blocked close is recorded as `PARTIAL_SUCCESS`; rows are still upserted, but
no courses for the blocked provider are deactivated. Investigate the provider
counts before changing these thresholds. Validation rows skipped with
`--allow-partial` use the same status. After resolving the cause, an intentional
retry of that batch requires `--force` because partial writes are idempotently
treated as already applied.
