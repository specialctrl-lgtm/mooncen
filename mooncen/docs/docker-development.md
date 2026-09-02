# Docker development stack

This document covers the local and an2p development half of MoonCen's
container release flow. It runs a production-shaped web stack with:

- PostgreSQL 16 + PostGIS on an internal Compose network
- an idempotent, one-shot schema migration service
- the FastAPI application on `127.0.0.1:8001`
- the built React application behind a non-root Nginx proxy on
  `127.0.0.1:5174`

Crawler workers, the crawler-control database, the Ops Console, monitoring,
backups, PostgreSQL, nginx, Cloudflare, and the deployment transport remain
outside the application containers. The reviewed API and frontend images are
built exactly once, validated on an2p, and only then may the same image IDs be
promoted by the production controller. See
[`docker-production.md`](docker-production.md) for that second half. Production
deployment and backup tooling is never copied into the API image; the image
receives only the Docker-specific API-login provisioner from `deploy/`.

> **Rollout hold (2026-08-20):** Do not run the an2p root bootstrap, phase 1,
> or any production promotion from this document until an incident-fixed
> replacement review snapshot has passed independent review. The previously
> promoted ref
> `refs/mooncen/docker-release-snapshots/223fef9f6786da960faf9951324650ad`
> is invalid and must not be reused, even if it still resolves locally.

## Prerequisites

- Docker Desktop or Docker Engine
- Docker Compose v2.35.0 or newer (required by the smoke model's safe
  `--no-env-resolution` inspection)
- At least 4 GiB available memory

On the reviewed Ubuntu 24.04 an2p host, use the distribution packages rather
than a curl-pipe installer:

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 docker-buildx
sudo systemctl enable --now docker
```

Do not add `sgm`, an API account, or an interactive operator to `docker` or
`lxd`; both groups are host-root capabilities. The reviewed runtime installer
creates the nologin `mooncen_docker_operator` account and makes it the only
non-root member of `docker`. The initial host contract also requires no
`/etc/docker/daemon.json` and no `docker.service.d` override; either needs a
separate host review before rollout.

The images are native multi-architecture images. The PostGIS image is built on
the official PostgreSQL 16 image and installs the PGDG PostGIS package, so an
Apple Silicon Mac does not need `platform: linux/amd64` emulation.

## Configure

From the repository root:

```powershell
$DockerConfig = Join-Path $HOME ".config/mooncen-docker"
$DockerEnv = Join-Path $DockerConfig "development.env"
New-Item -ItemType Directory -Force $DockerConfig | Out-Null
Copy-Item deploy/docker/docker.env.example $DockerEnv
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Run the generator three times and put independent values into
`MOONCEN_DB_PASSWORD`, `MOONCEN_DB_API_PASSWORD`, and `MOONCEN_AUTH_SECRET` in
`~/.config/mooncen-docker/development.env`. Do not reuse production credentials. Compose
intentionally refuses to start while any value is empty. The first password is
held only by PostgreSQL and the one-shot migration container; FastAPI receives
only a separate login that inherits the `mooncen_api` permission group.

Only the allowlisted site URL, OAuth redirect, client IDs, and Kakao JavaScript
key are written to the runtime browser configuration. They are not frontend
build arguments, so the same image can be reused across environments. Server
REST keys, DB credentials, OAuth client secrets, SMTP credentials, and
Cloudflare tokens must never enter the browser config or Docker build arguments.

On macOS or Linux, use the equivalent commands:

```bash
docker_config="$HOME/.config/mooncen-docker"
docker_env="$docker_config/development.env"
install -d -m 0700 "$docker_config"
install -m 0600 deploy/docker/docker.env.example "$docker_env"
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Keep these local-experiment paths at user-owned mode `0700` and the environment
file at `0600`. Never place the environment in the repository; the example file
contains names only and the external copy contains local development
credentials. Persistent an2p does not consume this user-home environment. Its
reviewed root installer reads `/root/mooncen-an2p-bootstrap/docker-development.env`,
installs the exact environment inside the immutable runtime pair as
root:`mooncen_docker_operator` mode `0640`, and fixes the rendered public browser
config at `/var/lib/mooncen-docker-operator/runtime-config.js`. Every systemd
start verifies that installed copy and its activation digest.

## Local development experiment

```powershell
.venv/bin/python deploy/docker/verify_clean_source.py
docker compose --env-file $DockerEnv up --build -d
docker compose --env-file $DockerEnv ps
docker compose --env-file $DockerEnv logs migrate
```

The source verifier fails unless every Docker control file exists in `HEAD` and
all files under the Docker `COPY` inputs match that commit. The control set also
includes `.gitattributes` and `.gitignore`, because those files determine the
checked-out bytes and which local paths Git considers ignored. It reports only
a bounded list of Git status codes and relative filenames; it never prints file
contents or credentials. A successful local image build from an untracked file
is not clean-clone evidence.

Open:

- Web: <http://localhost:5174>
- API health: <http://localhost:8001/health>
- API documentation: <http://localhost:8001/docs>

Use `localhost` consistently in the browser. The containers bind only to the
host loopback address, but switching between `127.0.0.1` and `localhost` would
split OAuth PKCE/session storage across two browser origins.

The API starts only after `postgres` is healthy and `migrate` exits
successfully. Re-running `up` is safe because `DB/setup_db.py --mode migrate`
uses the existing migration ledger and advisory lock, then atomically converges
one Docker-managed, non-superuser API LOGIN role. An existing role with the
requested name is not repurposed unless it carries the Docker ownership marker.

Before using the stack for development, run the destructive-but-isolated smoke
test once. The smoke runs the same clean-source verifier before reserving ports,
probing the Docker daemon, or building an image:

```powershell
.venv/bin/python deploy/docker/smoke.py
```

For a deliberately local experiment with reviewed but uncommitted source, the
gate can be bypassed explicitly:

```powershell
.venv/bin/python deploy/docker/smoke.py --allow-dirty-source
```

This prints a warning and must not be recorded as clean-clone or release
evidence. CI never uses this escape hatch. It does not weaken the separate
remote-Docker-context refusal.

To cross-check the same full stack for Apple Silicon from a QEMU-enabled
amd64 Docker host, select the platform explicitly:

```powershell
.venv/bin/python deploy/docker/smoke.py --platform linux/arm64
```

On an Apple Silicon Mac the default smoke already builds and runs native
ARM64 images, so the flag is optional.

It creates a randomly named project with temporary credentials and unused
loopback ports, verifies three migration passes, all required extensions,
health, course and provider reads, OAuth/auth boundaries, the public/Ops
routing boundary on both the direct API port and frontend proxy,
least-privilege API database identity and denied writes,
non-root users, and read-only filesystems, then removes only its own containers,
volume, and temporary image tags. It refuses a remote or unreviewed Docker
context by default.

The official PostgreSQL entrypoint applies `POSTGRES_DB`, `POSTGRES_USER`, and
`POSTGRES_PASSWORD` only when the named volume is first created. If you change
the DB name or credentials later, update them inside PostgreSQL or deliberately
reset the local volume; changing `development.env` alone does not rewrite an
existing cluster. The Docker provisioner can rotate `MOONCEN_DB_API_PASSWORD`
on the next `up`; changing `MOONCEN_DB_API_USER` disables the previously
Docker-managed login before activating the replacement.

The database is not published to the host. Use the container-local client when
you need SQL access:

```powershell
docker compose --env-file $DockerEnv exec postgres `
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

On macOS or Linux:

```bash
docker compose --env-file "$docker_env" exec postgres \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

If ports `5174` or `8001` are already used by the Windows development
autostart task, stop that task first or choose unused `MOONCEN_WEB_PORT` and
`MOONCEN_API_PORT` values. When changing the web port, keep the site URL,
CORS origin, and OAuth redirect URI on that same port.

## Stop or reset

Stop containers while preserving the database:

```powershell
docker compose --env-file $DockerEnv down
```

`docker compose down -v` permanently removes the local database volume. Use it
only when a complete local reset is intentional.

## Persistent an2p development runtime

The commands in the local-development section above are experiments only. They
must not create or activate the persistent an2p runtime. Persistent installation
accepts only a reviewed Git snapshot and a root trust envelope, rebuilds it in a
clean checkout, and installs one immutable control/Docker runtime pair. The
developer account does not own that pair, the release evidence, or the Docker
daemon.

The persistent Docker unit is the system service `mooncen-docker-dev.service`.
It always runs Compose with `--no-build --pull never` under the dedicated
`mooncen_docker_operator` account. The `sgm` account must not belong to the
`docker` or `lxd` groups. User-home release directories, user-manager Docker
unit activation, user-owned runtime pointers, and executing an installer from
the mutable worktree are not supported deployment paths.

Create a Docker-specific review snapshot without changing the current index or
worktree, review its exact diff, and then promote that reviewed tree to a
dedicated release ref:

```bash
repo="$(pwd -P)"
review_branch="docker-dev-snapshot-$(date -u +%Y%m%d)"
.venv/bin/python deploy/docker/create_review_snapshot.py \
  --branch "$review_branch"

review_commit="$(git rev-parse "$review_branch^{commit}")"
base_commit="$(git rev-parse "$review_commit^1")"
source_tree="$(git rev-parse "$review_commit^{tree}")"
git diff --stat "$base_commit" "$review_commit"
git diff --check "$base_commit" "$review_commit"
# Complete human review of the exact diff before continuing.

release_ref="refs/mooncen/docker-release-snapshots/$(openssl rand -hex 16)"
.venv/bin/python deploy/docker/promote_review_snapshot.py \
  --review-commit "$review_commit" \
  --base-commit "$base_commit" \
  --source-tree "$source_tree" \
  --reference "$release_ref" \
  --confirmation "PROMOTE DOCKER ${source_tree:0:12}"
snapshot_commit="$(git rev-parse "$release_ref^{commit}")"
```

### Establish the root installer trust

The release approver supplies eight SHA-256 values out of band: the bootstrap
itself plus the seven files/policy named below. Do not derive those values from
the an2p checkout. Copy, verify, and execute only the root-owned bootstrap
stage. The `*_sha256` values below must be copied from the independent release
authorization.

The reviewed bootstrap owns the pre-trust security boundary. Before publishing
the installed entrypoint or mode-0600 trust envelope, it durably captures the
currently selected public development runtime, globally and locally masks the
old user Ops/worker/tunnel/Vite/status units, moves the four superseded shared
credential files into a root-only quarantine, removes `sgm` from `docker` and
`lxd`, and drains only `sgm` processes that still retain either captured old
GID. It never calls `loginctl terminate-user`. Each matching process is
revalidated and held by a pidfd, then receives `SIGSTOP` before any further
work. `SIGSTOP` cannot be caught, blocked, or ignored, so a host-root-capable
stale process gets no signal-handler window. The same pidfd receives `SIGKILL`;
recovery waits only for a bounded interval, rescans for old-GID processes, and
rechecks that `sgm` still has neither group membership. A newly connected clean
SSH session has neither old GID and survives.

The durable bootstrap journal advances monotonically through `prepared` →
`membership_revoked` → `privileged_processes_drained` → `native_restored` →
`trust_committed`. Recovery never repeats a completed destructive phase. It
restores captured existing `mooncen-api.service` and
`mooncen-frontend.service` directly and does not require
`mooncen-development-runtime.target` to exist. Phase 1 installs the reviewed
target later.

Before arming recovery, the bootstrap verifies the installer bytes against the
independently authorized exact installer SHA-256. It publishes those verified
bytes atomically at
`/var/lib/mooncen-an2p-runtime/reviewed-install-runtime-snapshot.sh` as
`root:root 0700`, fsyncing both the file and its parent directory. Recovery and
the final trust commit execute only this immutable root stage; they never
resolve or execute installer bytes from the mutable `sgm` worktree. Only after
the stage is durable does the bootstrap install and enable the exact root-owned
`mooncen-an2p-bootstrap-recovery.service`. It uses
`Restart=on-abnormal`, waits 30 seconds, permits at most one automatic restart,
and has a 15-minute start timeout. A signal or reboot can therefore resume the
same reviewed stage once; an explicit invariant, health, or installation
failure is fail-stop and requires diagnosis followed by a manual retry. The
unit disables itself only after quarantine, GID revocation and drain, public
health, installed installer bytes, and the trust envelope are all durable. The
reviewed installer stage is removed only after `trust_committed` succeeds; do
not delete either root stage while this recovery unit is enabled.

```bash
bootstrap_sha256='<reviewed-bootstrap-sha256>'
installer_sha256='<reviewed-installer-sha256>'
integrity_sha256='<reviewed-production-integrity-sha256>'
clean_source_sha256='<reviewed-clean-source-sha256>'
pair_manager_sha256='<reviewed-pair-manager-sha256>'
handoff_sha256='<reviewed-evidence-handoff-sha256>'
registrar_sha256='<reviewed-registrar-sha256>'
host_transition_sha256='<reviewed-host-transition-sha256>'
build_policy_sha256='<reviewed-build-policy-sha256>'
bootstrap_stage=/root/mooncen-an2p-runtime-bootstrap.sh

sudo /usr/bin/install -o root -g root -m 0700 \
  deploy/an2p/bootstrap_runtime_installer.sh "$bootstrap_stage"
printf '%s  %s\n' "$bootstrap_sha256" "$bootstrap_stage" | \
  sudo /usr/bin/sha256sum --check --strict -
sudo /usr/bin/systemd-run --wait --pipe --collect \
  --unit=mooncen-an2p-runtime-bootstrap \
  /usr/bin/env -i HOME=/root LANG=C LC_ALL=C \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /bin/bash "$bootstrap_stage" \
  --installer-sha256 "$installer_sha256" \
  --integrity-sha256 "$integrity_sha256" \
  --clean-source-sha256 "$clean_source_sha256" \
  --pair-manager-sha256 "$pair_manager_sha256" \
  --handoff-sha256 "$handoff_sha256" \
  --registrar-sha256 "$registrar_sha256" \
  --host-transition-sha256 "$host_transition_sha256" \
  --build-policy-sha256 "$build_policy_sha256"
```

The bootstrap must not close a clean SSH session. From that session, verify that
recovery reached `trust_committed`, the recovery unit is inactive and disabled,
the reviewed installer stage has been removed, and the installed entrypoint
digest matches; then delete the one-use bootstrap stage. If the unit failed,
preserve both stages and the journal, diagnose the explicit failure, and retry
manually. Never execute the checkout copy.

```bash
installer_sha256='<reviewed-installer-sha256>'
host_transition_sha256='<reviewed-host-transition-sha256>'
printf '%s  %s\n' "$installer_sha256" \
  /usr/local/sbin/mooncen-an2p-runtime-install | \
  sudo /usr/bin/sha256sum --check --strict -
printf '%s  %s\n' "$host_transition_sha256" \
  /usr/local/libexec/mooncen-an2p-host-transition | \
  sudo /usr/bin/sha256sum --check --strict -
sudo /usr/bin/rm -f -- /root/mooncen-an2p-runtime-bootstrap.sh
```

All installer actions run outside the `sgm` login cgroup with an empty
environment. The installer treats the bootstrap boundary as an assertion: it
does not postpone privilege revocation until after the trusted bytes exist.
Before the first build, have the trusted entrypoint generate the exact root-only
development input. It creates and then idempotently validates
`/root/mooncen-an2p-bootstrap/docker-development.env` as `root:root 0600` under
a `root:root 0700` parent. The schema contains fixed `mooncen`/`mooncen_admin`/
`mooncen_api_login` identities, localhost `5174` site/CORS/OAuth redirect values,
and exactly three independent 64-character CSPRNG values for the Docker DB owner,
Docker API login, and development auth signing key. It reads no cloud, production,
Ops, user-home, LXD, or native credential and never prints a value.

```bash
sudo /usr/bin/systemd-run --wait --pipe --collect \
  --unit=mooncen-an2p-prepare-development-bootstrap \
  /usr/bin/env -i HOME=/root LANG=C LC_ALL=C \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/local/sbin/mooncen-an2p-runtime-install \
  prepare-development-bootstrap
sudo stat -c '%U:%G:%a' \
  /root/mooncen-an2p-bootstrap \
  /root/mooncen-an2p-bootstrap/docker-development.env
```

The metadata output must be `root:root:700` then `root:root:600`. A retry accepts
only the exact existing canonical bytes and preserves the three values; a stale,
extra, reordered, duplicated, empty, reused, or malformed value fails closed.
Do not copy native LXD DB values: the Docker named volume and logins are a separate
development security domain.

On a clean host, install the Python copy-mode prerequisite once:

```bash
sudo /usr/bin/systemd-run --wait --pipe --collect \
  --unit=mooncen-an2p-runtime-prerequisites \
  /usr/bin/env -i HOME=/root LANG=C LC_ALL=C \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/local/sbin/mooncen-an2p-runtime-install bootstrap-prerequisites
```

The install action has an exact 11-argument contract. Values must come from the
reviewed release authorization; `build_policy` must equal the 64-hex digest
pinned in the root trust envelope. Argument reordering, extra arguments, a
short ref, or a mismatched commit/parent/tree/policy fails closed.

```bash
release_ref='refs/mooncen/docker-release-snapshots/<reviewed-32hex>'
snapshot_commit='<reviewed-snapshot-commit-40hex>'
base_commit='<reviewed-parent-commit-40hex>'
source_tree='<reviewed-source-tree-40hex>'
build_policy="<reviewed-build-policy-64hex>"
sudo /usr/bin/systemd-run --wait --pipe --collect \
  --unit=mooncen-an2p-runtime-install \
  /usr/bin/env -i HOME=/root LANG=C LC_ALL=C \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/local/sbin/mooncen-an2p-runtime-install install \
  --reference "$release_ref" \
  --commit "$snapshot_commit" \
  --base-commit "$base_commit" \
  --source-tree "$source_tree" \
  --build-policy "$build_policy"
```

If the reviewed pair changes the host-layer ABI, the ordinary `install` action
fails before changing any global helper or unit. Use the separate exact
15-argument maintenance contract only after recording the currently active
pair and its receipt-bound host-layer digest. The transition keeps the old pair
live until the new pair and publication journal are durable, moves through a
no-pair/native-health checkpoint, then rolls forward to the new Docker pair.
Its root-only recovery journal never performs a cross-ABI rollback.

```bash
previous_pair='runtime-pair.<previous-commit40>.<previous-tree40>.<previous-policy64>'
previous_host_layer='<previous-host-layer-sha256>'
sudo /usr/bin/systemd-run --wait --pipe --collect \
  --unit=mooncen-an2p-runtime-host-transition \
  /usr/bin/env -i HOME=/root LANG=C LC_ALL=C \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/local/sbin/mooncen-an2p-runtime-install install-host-transition \
  --reference "$release_ref" \
  --commit "$snapshot_commit" \
  --base-commit "$base_commit" \
  --source-tree "$source_tree" \
  --build-policy "$build_policy" \
  --from-pair "$previous_pair" \
  --from-host-layer "$previous_host_layer"
```

Phase 1 uses only the reviewed development environment. It does not require or
read the Ops API/worker/DB transport inputs in the root bootstrap directory.
The installer performs the following sequence itself; there is no operator
step that builds into `$HOME` or edits an installed environment.

1. Re-resolve the exact release ref, commit, parent, and tree from the fixed
   repository, then clone without local hardlinks.
2. Verify the clean-source set and the build-policy digest against the root
   envelope.
3. Build the API, frontend, development PostgreSQL image, and Ops static bundle
   once as `mooncen_docker_operator` using the pinned build inputs.
4. Run the isolated an2p smoke and create the canonical `an2p-dev` PASS receipt.
5. Seal `runtime-pair.<commit>.<tree>.<policy>` with control, Docker,
   environment, static-asset, and host-layer inventories.
6. Publish the exact four release files new-only at
   `/opt/mooncen-an2p-docker/evidence/<tree>` without contacting production or
   copying them into the worker release root.
7. Prepare the user/account/socket boundary while leaving the currently active
   native API/frontend untouched. This phase installs the reviewed
   `mooncen-development-runtime.target`; bootstrap recovery itself does not
   depend on that target and restores pre-existing API/frontend units directly.
   The root pair manager then journals the
   cutover, atomically selects the pair, starts only persistent Docker, proves
   `8001`/`5174`, and fsyncs the exact pending-control-finalization receipt.
   A first-install failure or boot recovery restores reviewed native selection;
   an upgrade failure restores the exact prior selection and finalized control
   state. No Ops API, worker, DB tunnel, status agent, production SSH consumer,
   evidence registration, handoff, or production DB mutation occurs in phase 1.

For the first pair, bootstrap requires the reviewed native API and frontend to
be active and enabled. A legacy root Docker selection that still uses the
retired split alias is rejected before the recovery unit, privilege revocation,
or any runtime mutation; restore native first instead of asking rollback code
to reconstruct an alias that has no immutable pair.

The pair manager, the root selector, and phase-2 isolated installer share the
same root-owned operation lock. A manager child receives only an inherited,
inode-checked lock descriptor; an interactive `native-select`/`docker-select`
waits until the journal commit and refuses an interrupted journal. Phase 1,
finalization, and password-rotation success JSON is emitted only after the exact
Docker marker/unit/native-absence and `8001`/`5174` health are sampled under that
same fence.

The install command returns canonical JSON with `control_finalized=false` and
`development_healthy=true`. Treat that as the phase-1 commit point only after
the Docker unit and both health checks below also pass. The authoritative
phase-2 secret handoff and `finalize-control --pair "$pair"` command are in
[`deploy/an2p/README.md`](../deploy/an2p/README.md); do not invoke the isolated
installer directly.

```bash
pair="runtime-pair.<commit40>.<tree40>.<policy64>"
systemctl is-enabled --quiet mooncen-docker-dev.service
systemctl is-active --quiet mooncen-docker-dev.service
curl --noproxy '*' -fsS http://127.0.0.1:8001/health
curl --noproxy '*' -fsSI http://127.0.0.1:5174/
! systemctl is-active --quiet mooncen-ops-db-tunnel.service
! systemctl is-active --quiet mooncen-ops-api.service
! systemctl is-active --quiet mooncen-deployment-worker.service
! systemctl is-active --quiet mooncen-ops-status-agent.service
```

Only one pointer selects both reviewed control code and Docker runtime bytes:

```text
/opt/mooncen-an2p-runtime/current
  -> releases/runtime-pair.<commit40>.<tree40>.<policy64>
/opt/mooncen-an2p-control/current -> ../mooncen-an2p-runtime/current/control
/opt/mooncen-an2p-docker/current  -> ../mooncen-an2p-runtime/current/docker
```

The pair contents and receipt are immutable. A receipt cannot be refreshed in
place. A source, policy, environment, or public runtime-config change requires
a newly reviewed release and a fresh PASS receipt. Expiry does not stop an
already selected exact runtime, but an expired receipt cannot authorize another
production promotion.

After a reviewed pair is installed, the normal user wrapper may select that
already-installed Docker runtime. The wrapper cannot build or install a pair;
the fixed root helper owns mutual exclusion and verifies that the native units
are inactive before starting Docker.

```bash
cd /home/sgm/src/project/mooncen
/bin/bash ./deploy/an2p/install_user_services.sh \
  --development-runtime docker --restart
```

The system service validates the installed policy, environment, PASS receipt,
target identity, activation receipt, and local/running image IDs on every
start. It renders allowlisted browser values to
`/var/lib/mooncen-docker-operator/runtime-config.js` and mounts that file
read-only. It never reads executable bytes or secrets from the worktree or a
user home.

The Docker stack has its own named PostgreSQL volume. The existing LXD
`mooncen-dev-db`, its loopback proxy on `5432`, and its snapshots are not
modified or deleted; they remain the native rollback path. Stopping or
reloading `mooncen-docker-dev.service` uses Compose `stop`/`up` only. Neither
the unit nor the installer runs `down --volumes`.

```bash
systemctl status mooncen-docker-dev.service
journalctl -u mooncen-docker-dev.service -n 200 --no-pager
sudo /usr/local/libexec/mooncen-an2p-service-control runtime-status
```

To roll back the complete control/Docker pair, use only the trusted root
entrypoint with an exact retained pair name. Do not rewrite `current` symlinks.

```bash
sudo /usr/bin/systemd-run --wait --pipe --collect \
  --unit=mooncen-an2p-runtime-rollback \
  /usr/bin/env -i HOME=/root LANG=C LC_ALL=C \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/local/sbin/mooncen-an2p-runtime-install rollback \
  --pair "runtime-pair.<commit40>.<tree40>.<policy64>"
```

To select the preserved native API/frontend and LXD database without deleting
the Docker volume:

```bash
/bin/bash ./deploy/an2p/install_user_services.sh \
  --development-runtime native --restart
```

## Development and production boundaries

- Unreleased source changes require a local rebuild; persistent an2p and
  production never build from the current worktree. A changed source tree
  requires a new snapshot, release bundle, and PASS receipt.
- Every service has a CPU, memory, and PID ceiling and uses Docker's bounded
  `local` log driver (`10m` per file, three files). The API explicitly starts
  with the public-only route profile.
- The API root filesystem stays read-only. Crawler registry imports write only
  ephemeral diagnostics to the bounded `/app/logs` tmpfs; these logs disappear
  with the container and are not a crawler-worker persistence contract.
- The frontend image contains the reviewed sitemap present at build time. A
  sitemap change therefore creates a new source tree and image release.
- `/course/`, `/category/`, and `/branch/` are proxied to FastAPI so their
  server-rendered SEO HTML is preserved.
- The development `compose.yaml` is never used for production. Production uses
  a separate digest-bound template and container-aware guard while preserving
  host Cloudflare, nginx, PostgreSQL, backup, and deployment-SSH boundaries.
- Runtime development secrets are Compose environment values and are visible
  to users who can inspect the local Docker daemon. Production instead uses
  root-owned external environment/config files and its existing
  least-privilege application DB login; neither environment's secrets enter
  image layers or release evidence.
  PostgreSQL also uses its standard root entrypoint for first-volume
  ownership setup before dropping to the `postgres` user.
- A private Git remote protects repository visibility but does not revoke a
  leaked credential. Rotate any previously exposed credential before pushing.
  All runtime-imported untracked source files must also be reviewed and
  committed, otherwise a fresh clone will not build the current application.

## Private Git release gate

Build release images only from a clean, reviewed commit. Run
`python deploy/docker/verify_clean_source.py` before the smoke; it covers the
Compose/Docker/CI control files and every repository path copied by the API and
frontend Dockerfiles. Untracked, staged, modified, renamed, copied, or deleted
inputs all fail closed. Review and commit the application, configuration, and
database-migration files together with any Docker change rather than committing
only locally successful container files.

When a heavily modified worktree needs a review checkpoint, create a strictly
local WIP branch from only the verifier's control and Docker build-input paths:

```powershell
python deploy/docker/create_review_snapshot.py `
  --branch docker-dev-snapshot-20260816
```

Use the current date in the required `docker-dev-snapshot-YYYYMMDD` name. The
tool starts a temporary alternate Git index from `HEAD`, captures the selected
worktree state through Git's normal text normalization, creates a fixed-message
WIP commit, and creates the new local ref atomically. It does not switch
branches or modify the current
worktree, index, staged state, or `HEAD`. Git-ignored files are not forced into
the snapshot, and credential-like checks report only a bounded filename list.

This is a local review aid, not a clean release, backup, or authorization to
push. The tool deliberately has no push operation and no custom commit-message
option. Review every changed file, inspect infrastructure metadata, run a
dedicated secret scanner, clone the WIP branch into a separate directory, and
pass the clean-source verifier plus the full smoke there before considering a
private remote. Never deploy, merge, or push the generated branch as-is.

Keep the Git repository and image registry private, and retain the clean-clone
build plus migration/health smoke as a merge gate. The SHA-pinned `Docker
development stack` workflow runs the verifier explicitly in each clean checkout,
then runs an amd64 Compose migration/health smoke and the same full stack for
Linux ARM64.
