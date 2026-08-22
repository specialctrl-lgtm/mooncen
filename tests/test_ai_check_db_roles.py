from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _column_grant(sql: str, privilege: str, role: str) -> set[str]:
    match = re.search(
        rf"GRANT\s+{privilege}\s+\((.*?)\)\s+ON\s+courses\s+TO\s+{role}\s*;",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert match, f"missing {privilege} column grant for {role}"
    return {column.strip().lower() for column in match.group(1).split(",") if column.strip()}


def test_ai_role_has_only_the_pipeline_course_columns() -> None:
    roles = _text("DB/roles.sql")
    selected = _column_grant(roles, "SELECT", "mooncen_ai")
    updated = _column_grant(roles, "UPDATE", "mooncen_ai")

    assert selected == {
        "id",
        "provider",
        "title",
        "title_raw",
        "title_prefix_removed",
        "target",
        "target_age_group",
        "target_min_age",
        "target_max_age",
        "target_with_parent",
        "target_tags",
        "target_age_is_explicit",
        "description",
        "category_raw",
        "schedule_raw",
        "is_ai_processed",
        "ai_title_processed",
        "ai_title_confidence",
        "ai_title_result",
        "ai_category",
        "ai_tags",
        "ai_summary",
        "is_active",
        "updated_at",
        "created_at",
    }
    assert updated == {
        "title",
        "target",
        "target_age_group",
        "target_min_age",
        "target_max_age",
        "target_with_parent",
        "target_tags",
        "target_age_is_explicit",
        "title_prefix_removed",
        "ai_title_processed",
        "ai_title_confidence",
        "ai_title_result",
        "ai_category",
        "ai_tags",
        "ai_summary",
        "is_ai_processed",
        "updated_at",
    }
    assert updated <= selected
    assert {"raw_url", "branch_id", "price", "fee", "view_count", "service_group"}.isdisjoint(selected | updated)
    assert "GRANT INSERT" not in "\n".join(line for line in roles.splitlines() if "mooncen_ai" in line)
    assert "GRANT DELETE" not in "\n".join(line for line in roles.splitlines() if "mooncen_ai" in line)
    assert "GRANT SELECT ON branches, courses TO mooncen_ai" not in roles
    assert "TO mooncen_api, mooncen_crawler, mooncen_applier, mooncen_ai" not in roles.split(
        "GRANT USAGE, SELECT ON ALL SEQUENCES", 1
    )[1].split(";", 1)[0]


def test_functional_check_role_is_read_only_on_courses_and_branches() -> None:
    roles = _text("DB/roles.sql")

    assert "GRANT SELECT ON branches, courses TO mooncen_check;" in roles
    assert "GRANT INSERT, UPDATE, DELETE ON branches, courses TO mooncen_check" not in roles
    assert "GRANT INSERT, UPDATE ON branches, courses TO mooncen_check" not in roles
    assert "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO mooncen_check" not in roles
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA public TO mooncen_check" not in roles


def test_permission_groups_converge_before_minimal_grants() -> None:
    roles = _text("DB/roles.sql")
    statements = [statement for statement in roles.split(";") if statement.strip()]

    def _revoke_statement(object_kind: str, role: str) -> str:
        marker = f"REVOKE ALL PRIVILEGES ON ALL {object_kind} IN SCHEMA public"
        return next(
            statement
            for statement in statements
            if marker in statement and role in statement.split("FROM", 1)[-1]
        )

    for role in ("mooncen_ai", "mooncen_check"):
        assert f"CREATE ROLE {role} NOLOGIN" in roles
        assert (
            f"ALTER ROLE {role} WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;"
            in roles
        )
        assert role in _revoke_statement("TABLES", role)
        assert role in _revoke_statement("SEQUENCES", role)
        assert role in roles.split("REVOKE ALL PRIVILEGES ON SCHEMA public", 1)[1].split(";", 1)[0]
        assert role in roles.split("REVOKE ALL PRIVILEGES ON DATABASE %I", 1)[1].split("'", 1)[0]

    assert roles.index(_revoke_statement("TABLES", "mooncen_ai")) < roles.index(
        "ON courses TO mooncen_ai"
    )
    assert roles.index(_revoke_statement("TABLES", "mooncen_check")) < roles.index(
        "GRANT SELECT ON branches, courses TO mooncen_check"
    )


def test_login_provisioning_assigns_one_intended_group_and_revokes_stale_access() -> None:
    provision = _text("DB/provision_login_roles.sql")

    for prefix, group in (("ai", "mooncen_ai"), ("check", "mooncen_check")):
        assert f"db_{prefix}_user, db_{prefix}_password_b64" in provision
        assert f":'db_{prefix}_user'" in provision
        assert f"decode(:'db_{prefix}_password_b64', 'base64')" in provision
        assert f"SELECT format('GRANT {group} TO %I', :'db_{prefix}_user') \\gexec" in provision

    membership_block = provision.split("FROM pg_auth_members membership", 1)[1].split("\\gexec", 1)[0]
    assert ":'db_ai_user'" in membership_block
    assert ":'db_check_user'" in membership_block
    shared_role_blocks = [
        block
        for block in provision.split("\\gexec")
        if "AS requested(role_name)" in block
    ]
    assert shared_role_blocks
    for block in shared_role_blocks:
        assert "(:'db_ai_user')" in block
        assert "(:'db_check_user')" in block
    assert "REVOKE ALL PRIVILEGES ON DATABASE" in provision
    assert "GRANT CONNECT ON DATABASE" in provision
