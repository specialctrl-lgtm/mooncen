from __future__ import annotations

from tools import cleanup_ended_courses as cleanup_module


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class _Connection:
    def __init__(self):
        self.autocommit = True
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.cursor_instance = _Cursor()

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_cleanup_uses_primary_config_and_serializes_with_staging_apply(monkeypatch):
    connection = _Connection()
    config_calls = []
    lock_calls = []
    lifecycle_calls = []

    def fake_config(prefix, default_name):
        config_calls.append((prefix, default_name))
        return {"database": "primary"}

    monkeypatch.setattr(cleanup_module, "db_config", fake_config)
    monkeypatch.setattr(cleanup_module, "connect", lambda config: connection)
    monkeypatch.setattr(
        cleanup_module,
        "acquire_primary_apply_lock",
        lambda conn: lock_calls.append(conn),
    )
    monkeypatch.setattr(
        cleanup_module,
        "apply_ended_course_lifecycle",
        lambda *, grace_days, cursor: (
            lifecycle_calls.append((grace_days, cursor))
            or {"closed": 12, "deactivated": 4}
        ),
    )

    result = cleanup_module.cleanup_ended_courses(grace_days=7)

    assert result == {"closed": 12, "deactivated": 4}
    assert config_calls == [("PRIMARY", "mooncen")]
    assert lock_calls == [connection]
    assert lifecycle_calls == [(7, connection.cursor_instance)]
    assert connection.autocommit is False
    assert connection.committed is True
    assert connection.rolled_back is False
    assert connection.closed is True


def test_cleanup_rolls_back_primary_transaction_on_failure(monkeypatch):
    connection = _Connection()
    monkeypatch.setattr(cleanup_module, "db_config", lambda *_args: {})
    monkeypatch.setattr(cleanup_module, "connect", lambda _config: connection)
    monkeypatch.setattr(cleanup_module, "acquire_primary_apply_lock", lambda _conn: None)

    def fail_cleanup(**_kwargs):
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(cleanup_module, "apply_ended_course_lifecycle", fail_cleanup)

    try:
        cleanup_module.cleanup_ended_courses()
    except RuntimeError as exc:
        assert str(exc) == "cleanup failed"
    else:
        raise AssertionError("cleanup failure must propagate")

    assert connection.committed is False
    assert connection.rolled_back is True
    assert connection.closed is True


def test_ops_cleanup_action_targets_primary_applier_environment():
    from tools import ops_service_action

    assert ops_service_action.ACTION_ACCOUNT_ENV[
        ops_service_action.PRIMARY_LIFECYCLE_ACTION
    ] == (
        "mooncen-applier",
        ops_service_action.Path("/etc/mooncen/applier.env"),
    )
    assert ops_service_action._required_service_keys(
        ops_service_action.PRIMARY_LIFECYCLE_ACTION
    ) == (
        "PRIMARY_DB_HOST",
        "PRIMARY_DB_PORT",
        "PRIMARY_DB_NAME",
        "PRIMARY_DB_USER",
        "PRIMARY_DB_PASSWORD",
        "DB_SSLROOTCERT",
    )
