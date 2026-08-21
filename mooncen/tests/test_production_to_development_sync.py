from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import tools.sync_production_to_development as sync


RUN_ID = "20260806T120000Z-deadbeef"
SOURCE_PASSWORD = "source-password-must-never-be-logged"
DESTINATION_PASSWORD = "destination-password-must-never-be-logged"
TOOLS = sync.ToolPaths(psql="psql", pg_dump="pg_dump", pg_restore="pg_restore")


def config() -> sync.SyncConfig:
    source = sync.DatabaseEndpoint(
        host="cloud",
        port=5432,
        database="mooncen",
        user="mooncen_sync_reader",
        password=SOURCE_PASSWORD,
        sslmode="require",
    )
    destination = sync.DatabaseEndpoint(
        host="localhost",
        port=5432,
        database="mooncen_dev",
        user="mooncen_dev_owner",
        password=DESTINATION_PASSWORD,
        sslmode="prefer",
    )
    administrator = sync.DatabaseEndpoint(
        host="localhost",
        port=5432,
        database="postgres",
        user="mooncen_dev_admin",
        password=DESTINATION_PASSWORD,
        sslmode="prefer",
    )
    return sync.SyncConfig(
        source=source,
        destination=destination,
        administrator=administrator,
        destination_environment="development",
    )


def identity(*, database: str, address: str, comment: str = "") -> dict:
    return {
        "database": database,
        "user": "role",
        "server_address": address,
        "server_port": 5432,
        "cluster_catalog_fingerprint": (
            "source-cluster" if database == "mooncen" else "development-cluster"
        ),
        "transaction_read_only": "on",
        "role_is_superuser": False,
        "role_can_create_database_objects": False,
        "role_can_write_tables": False,
        "environment_setting": "",
        "database_comment": comment,
    }


def completed(command, *, stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


class SuccessfulRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, command, *, env, check, capture_output, text, encoding, errors):
        assert check is False
        assert capture_output is True
        assert text is True
        assert encoding == "utf-8"
        assert errors == "strict"
        command = list(command)
        self.calls.append((command, dict(env)))
        tool = Path(command[0]).stem
        if tool == "pg_dump":
            output = Path(command[command.index("--file") + 1])
            output.write_bytes(b"PGDMP\x01private-data-excluded")
            return completed(command)
        if tool == "pg_restore":
            return completed(command)
        sql = command[command.index("--command") + 1]
        database = env["PGDATABASE"]
        if "transaction_read_only" in sql:
            if database == "mooncen":
                result = identity(database="mooncen", address="10.0.0.8")
            else:
                result = identity(
                    database=database,
                    address="127.0.0.1",
                    comment=(
                        "mooncen.environment=development; sanitized=true; "
                        "production_credentials=false"
                    ),
                )
            return completed(command, stdout=json.dumps(result) + "\n")
        if "json_object_agg(requested.name" in sql:
            return completed(command, stdout=json.dumps({"databases": {}}) + "\n")
        if "mooncen_sync_sanitized_tables" in sql:
            result = {
                "policy_version": sync.POLICY_VERSION,
                "tables": [{"table": "users", "removed_rows": 0, "remaining_rows": 0}],
                "remaining_sensitive_rows": 0,
                "residual_tables": [],
            }
            return completed(command, stdout=json.dumps(result) + "\n")
        if "mooncen_sync_sensitive_residual" in sql:
            result = {
                "policy_version": sync.POLICY_VERSION,
                "remaining_sensitive_rows": 0,
                "residual_tables": [],
            }
            return completed(command, stdout=json.dumps(result) + "\n")
        return completed(command)


def manifest_document(directory: Path) -> dict:
    paths = list(directory.glob("*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text(encoding="utf-8"))


def test_plan_writes_a_structured_secret_free_manifest_without_connecting(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "manifests"

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("plan mode must not start PostgreSQL tools")

    result = sync.synchronize(
        config=config(),
        mode="plan",
        confirmation=None,
        manifest_directory=manifest_dir,
        run_func=must_not_run,
        run_id=RUN_ID,
    )

    assert result["status"] == "planned"
    document = manifest_document(manifest_dir)
    assert document["schema_version"] == 1
    assert document["status"] == "planned"
    assert document["planned_phases"] == list(sync.PLAN_PHASES)
    serialized = json.dumps(document)
    assert SOURCE_PASSWORD not in serialized
    assert DESTINATION_PASSWORD not in serialized
    assert "postgresql://" not in serialized
    assert document["source"]["read_only"] is True
    assert document["safety"]["confirmation_verified"] is False


@pytest.mark.parametrize(
    ("host", "database", "user"),
    (
        ("cloud", "mooncen_dev", "developer"),
        ("localhost", "mooncen_production", "developer"),
        ("localhost", "mooncen_dev", "prod_admin"),
    ),
)
def test_destination_production_or_cloud_markers_are_hard_failures(
    host: str,
    database: str,
    user: str,
) -> None:
    current = config()
    unsafe_destination = sync.DatabaseEndpoint(
        host=host,
        port=5432,
        database=database,
        user=user,
        password=DESTINATION_PASSWORD,
    )
    unsafe = sync.SyncConfig(
        source=current.source,
        destination=unsafe_destination,
        administrator=sync.DatabaseEndpoint(
            host=host,
            port=5432,
            database="postgres",
            user="admin",
            password=DESTINATION_PASSWORD,
        ),
        destination_environment="development",
    )
    with pytest.raises(sync.SyncSafetyError):
        sync.validate_offline_safety(unsafe)


def test_same_live_server_is_rejected_even_when_database_names_differ() -> None:
    current = config()
    with pytest.raises(sync.SyncSafetyError, match="same database server"):
        sync.validate_live_identities(
            current,
            identity(database="mooncen", address="10.0.0.8"),
            identity(database="mooncen_dev", address="10.0.0.8"),
        )


def test_same_cluster_fingerprint_is_rejected_even_when_addresses_differ() -> None:
    current = config()
    source = identity(database="mooncen", address="10.0.0.8")
    destination = identity(database="mooncen_dev", address="127.0.0.1")
    destination["cluster_catalog_fingerprint"] = source["cluster_catalog_fingerprint"]
    with pytest.raises(sync.SyncSafetyError, match="same PostgreSQL cluster"):
        sync.validate_live_identities(current, source, destination)


@pytest.mark.parametrize(
    "guard",
    ("role_is_superuser", "role_can_create_database_objects", "role_can_write_tables"),
)
def test_source_login_must_be_a_dedicated_read_only_role(guard: str) -> None:
    current = config()
    source = identity(database="mooncen", address="10.0.0.8")
    source[guard] = True
    with pytest.raises(sync.SyncSafetyError, match="dedicated read-only role"):
        sync.validate_live_identities(
            current,
            source,
            identity(database="mooncen_dev", address="127.0.0.1"),
        )


def test_execute_requires_exact_destination_confirmation() -> None:
    current = config()
    for confirmation in (None, "mooncen", "MOONCEN_DEV"):
        with pytest.raises(sync.SyncSafetyError, match="exact destination database name"):
            sync.validate_confirmation(current, confirmation)
    sync.validate_confirmation(current, "mooncen_dev")


def test_dsn_configuration_is_refused_and_password_is_not_required_for_plan() -> None:
    env = {
        "MOONCEN_SYNC_SOURCE_HOST": "source.example.test",
        "MOONCEN_SYNC_SOURCE_DATABASE": "mooncen",
        "MOONCEN_SYNC_SOURCE_USER": "reader",
        "MOONCEN_SYNC_DEST_HOST": "localhost",
        "MOONCEN_SYNC_DEST_DATABASE": "mooncen_dev",
        "MOONCEN_SYNC_DEST_USER": "owner",
        "MOONCEN_SYNC_DEST_ENVIRONMENT": "development",
    }
    loaded = sync.load_config(env, mode="plan")
    assert loaded.source.password == ""
    with pytest.raises(sync.SyncSafetyError, match="DSN"):
        sync.load_config({**env, "MOONCEN_SYNC_SOURCE_DSN": "postgresql://secret"}, mode="plan")


def test_source_child_process_is_forced_read_only_and_receives_no_ambient_dsn(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://should-not-reach-child")
    monkeypatch.setenv("UNRELATED_API_TOKEN", "secret-token")
    environment = sync._libpq_environment(
        config().source,
        read_only=True,
        application_name="test",
    )
    assert environment["PGPASSWORD"] == SOURCE_PASSWORD
    assert environment["PGOPTIONS"] == "-c default_transaction_read_only=on"
    assert "DATABASE_URL" not in environment
    assert "UNRELATED_API_TOKEN" not in environment


def test_execute_uses_private_exclusions_sanitizes_then_swaps_and_cleans_archive(
    tmp_path: Path,
) -> None:
    runner = SuccessfulRunner()
    manifest_dir = tmp_path / "manifests"
    temporary_root = tmp_path / "temporary"
    temporary_root.mkdir()

    result = sync.synchronize(
        config=config(),
        mode="execute",
        confirmation="mooncen_dev",
        manifest_directory=manifest_dir,
        temporary_root=temporary_root,
        tools=TOOLS,
        run_func=runner,
        run_id=RUN_ID,
    )

    assert result["status"] == "succeeded"
    dump_command, dump_environment = next(
        (command, env) for command, env in runner.calls if Path(command[0]).stem == "pg_dump"
    )
    assert dump_environment["PGOPTIONS"] == "-c default_transaction_read_only=on"
    assert dump_environment["PGPASSWORD"] == SOURCE_PASSWORD
    for pattern in sync.DUMP_EXCLUDED_DATA_PATTERNS:
        assert pattern in dump_command
    assert not list(temporary_root.iterdir())

    document = manifest_document(manifest_dir)
    assert document["status"] == "succeeded"
    assert document["archive"]["sha256"]
    assert document["result"]["cleanup_completed"] is True
    assert document["safety"]["confirmation_verified"] is True
    phase_names = [phase["name"] for phase in document["phases"]]
    assert phase_names.index("sanitize_private_data") < phase_names.index(
        "replace_development_database_activate"
    )
    assert phase_names.index("verify_replacement") < phase_names.index(
        "remove_previous_development_database_drop"
    )
    serialized = json.dumps(document)
    assert SOURCE_PASSWORD not in serialized
    assert DESTINATION_PASSWORD not in serialized
    assert "postgresql://" not in serialized


def test_pg_dump_failure_is_propagated_without_stderr_or_secret_in_manifest(tmp_path: Path) -> None:
    def failing_dump(command, *, env, check, capture_output, text, encoding, errors):
        assert check is False and capture_output is True and text is True
        assert encoding == "utf-8" and errors == "strict"
        tool = Path(command[0]).stem
        if tool == "psql":
            database = env["PGDATABASE"]
            result = (
                identity(database="mooncen", address="10.0.0.8")
                if database == "mooncen"
                else identity(database="mooncen_dev", address="127.0.0.1")
            )
            return completed(command, stdout=json.dumps(result) + "\n")
        assert tool == "pg_dump"
        return completed(
            command,
            returncode=9,
            stderr=f"postgresql://reader:{SOURCE_PASSWORD}@cloud/mooncen",
        )

    manifest_dir = tmp_path / "manifests"
    temporary_root = tmp_path / "temporary"
    temporary_root.mkdir()
    with pytest.raises(sync.SyncRunError) as captured:
        sync.synchronize(
            config=config(),
            mode="execute",
            confirmation="mooncen_dev",
            manifest_directory=manifest_dir,
            temporary_root=temporary_root,
            tools=TOOLS,
            run_func=failing_dump,
            run_id=RUN_ID,
        )

    assert captured.value.status == "failed"
    assert not list(temporary_root.iterdir())
    document = manifest_document(manifest_dir)
    failed_phase = next(phase for phase in document["phases"] if phase["name"] == "source_read_only_dump")
    assert failed_phase["returncode"] == 9
    serialized = json.dumps(document)
    assert SOURCE_PASSWORD not in serialized
    assert "postgresql://" not in serialized


def test_pg_restore_failure_drops_only_the_tool_created_isolated_database(tmp_path: Path) -> None:
    runner = SuccessfulRunner()

    def failing_restore(command, **kwargs):
        if Path(command[0]).stem == "pg_restore":
            runner.calls.append((list(command), dict(kwargs["env"])))
            return completed(command, returncode=7, stderr=DESTINATION_PASSWORD)
        return runner(command, **kwargs)

    manifest_dir = tmp_path / "manifests"
    temporary_root = tmp_path / "temporary"
    temporary_root.mkdir()
    with pytest.raises(sync.SyncRunError):
        sync.synchronize(
            config=config(),
            mode="execute",
            confirmation="mooncen_dev",
            manifest_directory=manifest_dir,
            temporary_root=temporary_root,
            tools=TOOLS,
            run_func=failing_restore,
            run_id=RUN_ID,
        )

    document = manifest_document(manifest_dir)
    phases = {phase["name"]: phase for phase in document["phases"]}
    assert phases["restore_isolated_database"]["returncode"] == 7
    assert phases["cleanup_isolated_database_drop"]["status"] == "succeeded"
    assert "replace_development_database_preserve_previous" not in phases
    assert not list(temporary_root.iterdir())


def test_sanitization_policy_covers_account_oauth_tokens_sessions_and_ops_data() -> None:
    assert "public.users" in sync.DUMP_EXCLUDED_DATA_PATTERNS
    assert "public.oauth_accounts" in sync.DUMP_EXCLUDED_DATA_PATTERNS
    assert "public.ops_*" in sync.DUMP_EXCLUDED_DATA_PATTERNS
    assert "public.notifications" in sync.DUMP_EXCLUDED_DATA_PATTERNS
    assert "fcm_token" in sync.SANITIZE_SQL
    assert "access_token" in sync.SANITIZE_SQL
    assert "session_id" in sync.SANITIZE_SQL
    assert "information_schema.columns" in sync.SANITIZE_SQL
    assert "c.table_name = 'branches' AND c.column_name = 'phone'" in sync.SANITIZE_SQL
    assert "remaining_sensitive_rows" in sync.VERIFY_SANITIZATION_SQL
