from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_split_package_install_does_not_install_web_db_on_the_same_role() -> None:
    source = _read("deploy/ubuntu/install_split_packages.sh")

    db_block, web_block = source.split('if [ "$role" = "db" ]; then', 1)[1].split(
        'echo "MoonCen split DB packages installed."', 1
    )
    assert "postgresql" in db_block
    assert "postgresql-postgis" in db_block
    assert "nginx" not in db_block
    assert "install_verified_node" not in db_block
    assert "install_verified_node" in web_block
    assert 'if [ "$role" = "crawler" ]; then' in source
    crawler_block = source.split('if [ "$role" = "crawler" ]; then', 1)[1].split(
        'echo "MoonCen split crawler packages installed."', 1
    )[0]
    assert "postgresql-postgis" in crawler_block
    assert "install_verified_chrome_for_testing" in crawler_block
    assert "nginx" not in crawler_block
    assert "install_verified_node" not in crawler_block


def test_split_db_is_tls_only_and_limits_api_access_to_web_host() -> None:
    source = _read("deploy/ubuntu/setup_split_db.sh")

    assert "hostssl $DB_NAME $DB_API_USER $web_address scram-sha-256" in source
    assert "hostssl $DB_NAME $DB_APPLIER_USER $crawler_address scram-sha-256" in source
    assert "hostssl $DB_NAME $DB_CHECK_USER $crawler_address scram-sha-256" in source
    assert "hostssl $DB_NAME $DB_CRAWLER_USER $crawler_address" not in source
    assert 'crawler_client_env="$CONFIG_DIR/db-client-crawler.env"' in source
    assert "DB_SSLMODE=verify-full" in source
    assert "listen_addresses = '127.0.0.1,$bind_address'" in source
    assert "--clean" in source
    assert "c.relkind = 'S' AND d.deptype IN ('a', 'i')" in source
    assert "0.0.0.0/0" not in source


def test_split_web_uses_remote_db_tls_and_loopback_origins() -> None:
    source = _read("deploy/ubuntu/setup_split_web.sh")
    nginx = _read("deploy/ubuntu/nginx/mooncen_split_web.conf")

    assert "DB_SSLMODE=verify-full" in source
    assert 'groupadd --system "$service_user"' in source
    assert '--gid "$service_user"' in source
    assert 'chmod -R g+rX "$APP_DIR"' in source
    assert 'chmod -R g-w,o-rwx "$APP_DIR"' in source
    assert "timeout 30s tailscale serve" in source
    assert "--https=443" in source
    assert "http://127.0.0.1:80" in source
    assert 'rm -rf -- "$node_modules_dir"' in source
    assert "systemctl disable --now mooncen-frontend" in source
    assert "root /opt/mooncen/frontend2/dist;" in nginx
    assert "proxy_pass http://127.0.0.1:5173" not in nginx
    assert "try_files $uri $uri/ /index.html;" in nginx
    assert "listen 127.0.0.1:80;" in nginx
    assert "listen 0.0.0.0" not in nginx


def test_split_crawler_uses_dedicated_staging_and_starts_fail_closed() -> None:
    source = _read("deploy/ubuntu/setup_split_crawler.sh")
    export_source = _read("deploy/ha/export_n100_crawler_settings.sh")
    staging = _read("deploy/ha/n100_crawler_staging_setup.sh")
    helper = _read("deploy/ubuntu/ops_service_helper.sh")
    crawler_env = source.split(
        'install_service_env crawler.env "$CRAWLER_USER" <<EOF',
        1,
    )[1].split("\nEOF\n", 1)[0]
    installed_units = source.split("for unit in \\\n", 1)[1].split("; do", 1)[0]

    assert "USE_DEDICATED_STAGING_CLUSTER=1" in source
    assert 'USE_DEDICATED_STAGING_CLUSTER="${USE_DEDICATED_STAGING_CLUSTER:-0}"' in staging
    assert (
        'if [ "$USE_DEDICATED_STAGING_CLUSTER" = "1" ] || '
        '[ "$LOCAL_DB_ROLE" = "standby" ]; then'
    ) in staging
    assert "CRAWL_WRITE_MODE=staging" in source
    assert "CRAWL_STAGING_DB_PORT=55432" in source
    assert "PRIMARY_DB_USER=$DB_APPLIER_USER" in source
    assert "standby|crawler)" in helper
    assert "config/production_crawler_providers.yaml" in source
    assert (
        'KAKAO_MAPS_REST_API_KEY="$(read_env_value '
        'KAKAO_MAPS_REST_API_KEY "$source_crawler_env" 1)"'
    ) in source
    assert "must contain a non-empty Kakao REST API key" in source
    assert "KAKAO_MAPS_REST_API_KEY=$KAKAO_MAPS_REST_API_KEY" in crawler_env
    assert "GOOGLE_MAPS_API_KEY" not in source
    assert "GOOGLE_MAPS_API_KEY" not in crawler_env
    assert "KAKAO_GEOCODE_MAX_REQUESTS_PER_RUN=1000" in crawler_env
    assert 'source_env=/etc/mooncen/crawler.env' in export_source
    assert 'index($0, "KAKAO_MAPS_REST_API_KEY=") == 1' in export_source
    assert "printf 'KAKAO_MAPS_REST_API_KEY=%s\\n' \"$maps_key\"" in export_source
    assert "mooncen-staging-apply-dry-run@.service" in installed_units
    assert "mooncen-staging-apply@.service" in installed_units
    assert '"$source_crawler_env"' in source.split(
        'for required_file in "$db_client_env" "$db_ca"', 1
    )[1].split("; do", 1)[0]
    assert "timedatectl show --property=Timezone --value" in source
    assert '!= "Asia/Seoul"' in source
    assert "systemctl start mooncen-staging-apply-dry-run.service" in source
    assert "mooncen-crawler.timer unexpectedly enabled before validation" in source
    assert "mooncen-staging-apply.timer unexpectedly enabled before validation" in source
    assert "systemctl enable --now mooncen-crawler.timer" not in source
    assert "systemctl enable --now mooncen-staging-apply.timer" not in source
    assert "mooncen-ops-bot" not in source
    assert "source-bot" not in source
    assert "MOONCEN_BOT_" not in source
    assert "deploy-archive-sha256 is required" in source
    assert "deploy-commit is required" in source
    assert '[ "$DB_HOST" != "cloud" ]' in source
    assert "DEPLOY_ARCHIVE_SHA256=$deploy_archive_sha256" in source
    venv_install = source.index('"$APP_DIR/.venv/bin/python" -I -m pip install')
    venv_group_fix = source.index(
        'chown -R "$deploy_user":mooncen "$APP_DIR/.venv"'
    )
    service_start = source.index(
        "systemctl start mooncen-staging-apply-dry-run.service"
    )
    assert venv_install < venv_group_fix < service_start


def test_n100_migration_backup_excludes_secret_environment_contents() -> None:
    source = _read("deploy/ha/backup_n100_for_gen1crawler.sh")

    assert "pg_dump" in source
    assert "pg_restore --list" in source
    assert 'install -d -o postgres -g postgres -m 0700 "$output_dir"' in source
    assert 'chown -R root:root "$output_dir"' in source
    assert "protected-env-fingerprints.txt" in source
    assert 'sha256sum "$protected_env"' in source
    assert 'cp "$protected_env"' not in source


def test_split_crawler_release_permissions_cover_the_application_root() -> None:
    source = _read("deploy/ubuntu/setup_split_crawler.sh")

    app_group_fix = source.index('chown -R "$deploy_user":mooncen "$APP_DIR"')
    venv_creation = source.index(
        'runuser -u "$deploy_user" -- python3 -I -m venv --clear "$APP_DIR/.venv"'
    )
    venv_group_fix = source.index(
        'chown -R "$deploy_user":mooncen "$APP_DIR/.venv"'
    )
    assert app_group_fix < venv_creation < venv_group_fix


def test_gen1db_cutover_backup_is_validated_before_db_changes() -> None:
    source = _read("deploy/ubuntu/backup_gen1db_before_crawler_cutover.sh")

    assert "pg_dump" in source
    assert "pg_restore --list" in source
    assert 'install -d -o postgres -g postgres -m 0700 "$output_dir"' in source
    assert "mooncen_schema_migrations" in source
    assert "db-ca-fingerprint.txt" in source
    assert 'chown -R root:root "$output_dir"' in source
