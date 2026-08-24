# Database Role Separation

`mooncen_admin` remains the migration owner only. Ubuntu deployment creates
separate LOGIN roles that inherit one of the NOLOGIN groups in `DB/roles.sql`:

- API: `mooncen_api`
- crawler staging writer: `mooncen_crawler`
- staging-to-primary apply worker: `mooncen_applier`
- encrypted backup: `mooncen_readonly`

Default LOGIN names are `mooncen_api_login`, `mooncen_crawler_login`,
`mooncen_applier_login`, and `mooncen_backup_login`. The group roles themselves
remain `NOLOGIN`.

Create passwords in the deployment secret store, grant the matching group, and
set the service-specific credentials:

- API: `DB_API_USER` / `DB_API_PASSWORD`
- primary AI/crawler maintenance: `DB_CRAWLER_USER` / `DB_CRAWLER_PASSWORD`
- staging crawler: `CRAWL_STAGING_DB_USER` / `CRAWL_STAGING_DB_PASSWORD`
- primary applier: `PRIMARY_DB_USER` / `PRIMARY_DB_PASSWORD`
- backup: `DB_BACKUP_USER` / `DB_BACKUP_PASSWORD`
- schema owner (operator shell only): `DB_MIGRATOR_USER` (`DB_USER` remains a
  compatible alias while running migration commands)

`setup_project.sh` writes only runtime credentials to `/opt/mooncen/.env`. The
migration password is stored with mode `0600` under the SSH deploy user's
`~/.config/mooncen/deploy-secrets.env`, outside the app tree. Backup credentials and
the public age recipient are installed as root in `/etc/mooncen/backup.env`
(mode `0640`, readable by the backup OS user, not by `mooncen`).

`setup_db.py` still performs schema changes only. `setup_project.sh` explicitly
runs `roles.sql` and `provision_login_roles.sql` as the PostgreSQL administrator
after migrations, then verifies positive and negative privileges. Re-running
deployment rotates only explicitly changed passwords and converges stale direct
grants/memberships without recreating data.

`mooncen_crawler` is intended for the staging database and can write crawl
batches plus course/branch collection data. `mooncen_applier` is intended for
the primary database and can write only course/branch data and owner-created
apply metadata. Do not reuse either credential across those two databases.
The standby staging schema is owned by `mooncen_staging_owner`, a separate
NOLOGIN role. PostgreSQL provisions it with `SET ROLE`, so no reusable staging
owner password exists or enters a service environment.

After assigning the roles, verify the negative permissions as well as the
positive path (for example, the crawler must not be able to read `users`, and
the API may update only `courses.view_count`, not course identity/content).
`setup_project.sh` installs the backup-only default table/sequence privileges
for the configured migration owner. Manual provisioning must repeat those
`ALTER DEFAULT PRIVILEGES FOR ROLE <owner>` statements after `roles.sql`.
