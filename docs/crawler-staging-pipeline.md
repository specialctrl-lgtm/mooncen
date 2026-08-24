# MoonCen Crawler Staging Pipeline

> Archived `n100` design note. `n100` is retired and this procedure must not be
> used for current production. The reviewed crawler owner is `gen1crawler`; use
> `docs/multi-server-deployment.md` and `deploy/ubuntu/GEN1_SPLIT.md` for the
> current placement and activation contract.

## Retired Target Architecture

MoonCen separates crawler writes from the production PostgreSQL primary.

```text
N100 crawler
-> N100 crawl staging DB
-> validation / normalization / deduplication
-> cloud primary staging snapshot
-> cloud primary production upsert
-> PostgreSQL replication
-> N100 read-only replica
```

## Server Roles

### cloud

- PostgreSQL primary.
- Production API/frontend host.
- Production database: `mooncen`.
- Receives validated crawler batches from N100.

### N100

- Runs crawlers.
- Has a local PostgreSQL standby replica for failover/read-only purposes.
- Has a separate crawler staging DB, normally `mooncen_staging`.
- Does not write crawler output into the replica DB.
- Can become the backup API server only after failover promotion.

## Databases

### Production DB

```text
cloud: mooncen
```

Production tables:

- `branches`
- `courses`
- user/auth/notification tables

### N100 Staging DB

```text
n100: mooncen_staging on PostgreSQL cluster mooncen_staging, port 55432
```

The staging DB uses the normal MoonCen schema plus:

- `crawl_batches`
- `crawl_batch_validation_errors`
- `crawl_batch_apply_logs`
- `crawl_staging.branch_snapshots`
- `crawl_staging.course_snapshots`
- `branches.crawl_batch_id`
- `courses.crawl_batch_id`

`crawl_batch_id` is set through the PostgreSQL session setting
`mooncen.crawl_batch_id`. `run_crawlers.py` generates this value per crawler
cycle and subprocess crawlers inherit it through `CRAWL_BATCH_ID`.
The staging schema also installs `BEFORE INSERT OR UPDATE` triggers on
`branches` and `courses` so existing rows updated by crawler upserts are moved
to the current batch as well.
Post-crawl maintenance jobs such as coordinate backfill, category backfill, and
ended-course cleanup run without `CRAWL_BATCH_ID`; they must not move old rows
into the current crawler batch.

## Setup

Run on N100:

```bash
cd /opt/mooncen
sudo bash deploy/ha/n100_crawler_staging_setup.sh
sudo systemctl restart mooncen-crawler
```

The setup script:

- Creates a separate local PostgreSQL cluster `mooncen_staging` on port `55432`
  if the normal PostgreSQL cluster is a standby/replica.
- Creates `mooncen_staging` if missing.
- Applies `DB/schema.sql`.
- Applies `DB/staging_schema.sql`.
- Removes crawler cloud-primary DB override files.
- Adds crawler staging DB systemd overrides.
- Configures `mooncen-staging-apply.service`.

## Crawler Write Mode

N100 crawler services should have:

```text
CRAWL_WRITE_MODE=staging
CRAWL_STAGING_DB_HOST=localhost
CRAWL_STAGING_DB_PORT=55432
CRAWL_STAGING_DB_NAME=mooncen_staging
```

Crawler code continues to use `DB.db_utils.get_db_cursor()`, but the connection
is redirected to the staging DB only when `CRAWL_WRITE_MODE=staging`.

## Validation And Apply

Dry-run:

```bash
mooncenctl staging-dry-run
```

Apply latest batch:

```bash
mooncenctl staging-apply
```

The default latest-batch selection chooses the newest eligible `COLLECTED`
batch or a `FAILED` batch that contains explicit partial-success ownership
evidence, ordered by `started_at` and then `created_at`. Lifecycle closure still
requires complete collection evidence. Limited sample runs and branch-filtered
runs can be checked or applied by passing `--batch-id` directly to
`tools/apply_staging_batch.py`; split-node activation additionally requires its
reviewed exact batch to be the same default selection and a complete
`COLLECTED` result.

Direct script usage:

```bash
python -X utf8 tools/apply_staging_batch.py --dry-run
python -X utf8 tools/apply_staging_batch.py
python -X utf8 tools/apply_staging_batch.py --batch-id <crawl_batch_id> --dry-run
python -X utf8 tools/apply_staging_batch.py --provider HOMEPLUS --dry-run
python -X utf8 tools/apply_staging_batch.py --batch-id <crawl_batch_id> --force
```

Environment variables for the apply step:

```text
CRAWL_STAGING_DB_HOST=localhost
CRAWL_STAGING_DB_PORT=55432
CRAWL_STAGING_DB_NAME=mooncen_staging
PRIMARY_DB_HOST=cloud
PRIMARY_DB_PORT=5432
PRIMARY_DB_NAME=mooncen
```

## Apply Rules

The apply service:

- Loads one `crawl_batch_id`.
- Validates required course fields:
  - `provider`
  - `provider_course_id`
  - `title`
- Uploads raw branch/course snapshots into `cloud.mooncen.crawl_staging`.
- Upserts branches with `INSERT ... ON CONFLICT (provider, branch_code) DO UPDATE`.
- Upserts courses with `INSERT ... ON CONFLICT (provider, provider_course_id) DO UPDATE`.
- Does not overwrite AI summary/title fields from crawler staging rows.
- Skips a real apply if the same `crawl_batch_id` already has a successful
  non-dry-run apply log, unless `--force` is used.
- Marks missing active courses as closed only when the crawler batch explicitly
  records `close_missing_enabled=true`. This is intended for full provider
  collection batches only. Limited sample runs, provider-filtered apply runs,
  and branch-filtered runs must not close missing courses.

When enabled, missing active rows for providers that produced at least one valid
course in the applied batch are marked as:

```sql
status = 'CLOSED',
is_active = false,
removed_at = now()
```

Rows are not hard-deleted.

## Logging

Apply results are written to:

```text
crawl_batch_apply_logs
```

Key counters:

- `inserted_count`
- `updated_count`
- `closed_count`
- `error_count`
- `dry_run`
- `status`
- `result`

Validation errors are written to:

```text
crawl_batch_validation_errors
```

## Rollback Behavior

`--dry-run` runs validation and calculates insert/update/closed counts inside
one transaction, writes no committed production data changes, then rolls back.
The dry-run result log is committed separately so Ops Console and journals can
show what was checked.

If a real apply fails, the production transaction is rolled back.

## Important Safety Rules

- Do not point crawlers at the N100 replica DB.
- Do not use the replica connection string for crawler writes.
- N100 replica is read-only and should only become writable after explicit
  failover promotion.
- Production API should run on N100 only after N100 DB promotion.
- Cloud primary remains the only production write target during normal operation.
