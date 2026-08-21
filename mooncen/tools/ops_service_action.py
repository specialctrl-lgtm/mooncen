from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

APP_DIR = Path(os.environ.get("MOONCEN_APP_DIR", "/opt/mooncen")).resolve()
MAX_INPUT_BYTES = 64 * 1024
DEFAULT_CRAWLER_PROVIDER_LIMIT = 5_000
MAX_CRAWLER_PROVIDER_LIMIT = 100_000
MAX_CRAWLER_PROVIDER_ENV_BYTES = 48 * 1024
MUNICIPAL_AGGREGATE_OWNER = "MUNICIPAL_RESERVATION_TARGETS"
AGGREGATE_PROVIDER_OWNERS = {
    "EXPERIENCE_TARGETS",
    MUNICIPAL_AGGREGATE_OWNER,
}
PROVIDER_NAME_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,49}")
BATCH_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
RUNTIME_HOME_PATTERN = re.compile(r"/tmp/mooncen-ops-runtime\.[A-Za-z0-9]{6}")
STAGING_PROMOTE_ACTION = "staging-promote-provider"
PRIMARY_LIFECYCLE_ACTION = "cleanup-ended-courses"

ACTION_ACCOUNT_ENV = {
    "crawler-provider": ("mooncen-crawler", Path("/etc/mooncen/crawler.env")),
    # The crawler writes to staging in production. Lifecycle cleanup must use
    # the narrowly scoped primary applier credentials or it appears successful
    # while leaving the public database unchanged.
    PRIMARY_LIFECYCLE_ACTION: ("mooncen-applier", Path("/etc/mooncen/applier.env")),
    "coordinate-backfill": ("mooncen-crawler", Path("/etc/mooncen/crawler.env")),
    "db-summary": ("mooncen-crawler", Path("/etc/mooncen/crawler.env")),
    "coordinate-summary": ("mooncen-crawler", Path("/etc/mooncen/crawler.env")),
    "crawler-config": ("mooncen-crawler", Path("/etc/mooncen/crawler.env")),
    "crawler-provider-summary": ("mooncen-crawler", Path("/etc/mooncen/crawler.env")),
    "replication-summary": ("mooncen-crawler", Path("/etc/mooncen/crawler.env")),
    STAGING_PROMOTE_ACTION: ("mooncen-applier", Path("/etc/mooncen/applier.env")),
    "ai-reset": ("mooncen-ai", Path("/etc/mooncen/ai.env")),
    "ai-reset-full": ("mooncen-ai", Path("/etc/mooncen/ai.env")),
    "ai-quality": ("mooncen-ai", Path("/etc/mooncen/ai.env")),
    "ollama-test": ("mooncen-ai", Path("/etc/mooncen/ai.env")),
    "sitemap": ("mooncen-crawler", Path("/etc/mooncen/crawler.env")),
}

DB_ACTIONS = {
    "crawler-provider",
    PRIMARY_LIFECYCLE_ACTION,
    "coordinate-backfill",
    "db-summary",
    "coordinate-summary",
    "crawler-provider-summary",
    "replication-summary",
    "ai-reset",
    "ai-reset-full",
    "ai-quality",
    "sitemap",
    STAGING_PROMOTE_ACTION,
}

AI_DB_ACTIONS = {
    "ai-reset",
    "ai-reset-full",
    "ai-quality",
}

BRANCH_FILTER_PROVIDERS = {
    "AK_PLAZA",
    "ELAND_RETAIL",
    "EMART",
    "GALLERIA",
    "HOMEPLUS",
    "HYUNDAI_DEPT",
    "LOTTE_MART",
    "SHINSEGAE_ACADEMY",
}

PAYLOAD_ACTIONS = {
    "crawler-provider",
    STAGING_PROMOTE_ACTION,
}


class ActionError(RuntimeError):
    pass


def _trusted_runtime_environment() -> dict[str, str]:
    if os.name != "posix":
        return {}
    runtime_home = str(os.environ.get("HOME") or "")
    if not RUNTIME_HOME_PATTERN.fullmatch(runtime_home):
        return {}
    expected = {
        "HOME": runtime_home,
        "TMPDIR": runtime_home,
        "TMP": runtime_home,
        "TEMP": runtime_home,
        "XDG_CACHE_HOME": f"{runtime_home}/.cache",
        "XDG_CONFIG_HOME": f"{runtime_home}/.config",
        "XDG_RUNTIME_DIR": runtime_home,
    }
    if any(os.environ.get(key) != value for key, value in expected.items()):
        return {}
    try:
        effective_uid = os.geteuid()
        for directory in (
            runtime_home,
            expected["XDG_CACHE_HOME"],
            expected["XDG_CONFIG_HOME"],
        ):
            metadata = os.lstat(directory)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != effective_uid
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                return {}
    except (AttributeError, OSError):
        return {}
    return expected


def _read_payload() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ActionError("operation input is too large")
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActionError("operation input must be one JSON object") from exc
    if not isinstance(value, dict):
        raise ActionError("operation input must be one JSON object")
    return value


def _required_service_keys(action: str) -> tuple[str, ...]:
    if action not in DB_ACTIONS:
        return ()
    if action == PRIMARY_LIFECYCLE_ACTION:
        return (
            "PRIMARY_DB_HOST",
            "PRIMARY_DB_PORT",
            "PRIMARY_DB_NAME",
            "PRIMARY_DB_USER",
            "PRIMARY_DB_PASSWORD",
            "DB_SSLROOTCERT",
        )
    if action == STAGING_PROMOTE_ACTION:
        return (
            "CRAWL_STAGING_DB_HOST",
            "CRAWL_STAGING_DB_PORT",
            "CRAWL_STAGING_DB_NAME",
            "CRAWL_STAGING_DB_USER",
            "CRAWL_STAGING_DB_PASSWORD",
            "PRIMARY_DB_HOST",
            "PRIMARY_DB_PORT",
            "PRIMARY_DB_NAME",
            "PRIMARY_DB_USER",
            "PRIMARY_DB_PASSWORD",
            "DB_SSLROOTCERT",
        )
    if action in AI_DB_ACTIONS:
        return (
            "DB_HOST",
            "DB_NAME",
            "DB_RUNTIME_USER",
            "DB_RUNTIME_PASSWORD",
            "DB_APPLICATION_NAME",
        )
    return ("DB_HOST", "DB_NAME", "DB_CRAWLER_USER", "DB_CRAWLER_PASSWORD")


def _validate_crawler_service_environment(action: str, values: dict[str, str]) -> None:
    if action != "crawler-provider":
        return

    write_mode = str(values.get("CRAWL_WRITE_MODE") or "").strip().lower()
    if write_mode not in {"", "direct", "staging"}:
        raise ActionError("crawler write mode is invalid")
    if write_mode != "staging":
        return

    required = (
        "CRAWL_STAGING_DB_HOST",
        "CRAWL_STAGING_DB_PORT",
        "CRAWL_STAGING_DB_NAME",
        "CRAWL_STAGING_DB_USER",
        "CRAWL_STAGING_DB_PASSWORD",
    )
    if any(not str(values.get(key) or "").strip() for key in required):
        raise ActionError("staging crawler service environment is incomplete")

    host = str(values["CRAWL_STAGING_DB_HOST"]).strip().lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise ActionError("staging crawler database must be local")
    try:
        port = int(str(values["CRAWL_STAGING_DB_PORT"]).strip())
    except ValueError as exc:
        raise ActionError("staging crawler database port is invalid") from exc
    if not 1 <= port <= 65_535:
        raise ActionError("staging crawler database port is invalid")
    for key in ("CRAWL_STAGING_DB_NAME", "CRAWL_STAGING_DB_USER"):
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", str(values[key]).strip()):
            raise ActionError("staging crawler database identity is invalid")


def _validate_applier_service_environment(action: str, values: dict[str, str]) -> None:
    if action == PRIMARY_LIFECYCLE_ACTION:
        try:
            primary_port = int(str(values["PRIMARY_DB_PORT"]).strip())
        except ValueError as exc:
            raise ActionError("primary applier database port is invalid") from exc
        if not 1 <= primary_port <= 65_535:
            raise ActionError("primary applier database port is invalid")
        for suffix in ("NAME", "USER"):
            if not re.fullmatch(
                r"[a-z_][a-z0-9_]{0,62}",
                str(values[f"PRIMARY_DB_{suffix}"]).strip(),
            ):
                raise ActionError("primary applier database identity is invalid")
        root_cert = Path(str(values["DB_SSLROOTCERT"]).strip())
        if not root_cert.is_absolute() or not root_cert.is_file() or root_cert.is_symlink():
            raise ActionError("primary database root certificate is unavailable")
        return
    if action != STAGING_PROMOTE_ACTION:
        return
    staging_host = str(values["CRAWL_STAGING_DB_HOST"]).strip().lower()
    if staging_host not in {"localhost", "127.0.0.1", "::1"}:
        raise ActionError("staging applier database must be local")
    for prefix in ("CRAWL_STAGING", "PRIMARY"):
        try:
            port = int(str(values[f"{prefix}_DB_PORT"]).strip())
        except ValueError as exc:
            raise ActionError("applier database port is invalid") from exc
        if not 1 <= port <= 65_535:
            raise ActionError("applier database port is invalid")
        for suffix in ("NAME", "USER"):
            if not re.fullmatch(
                r"[a-z_][a-z0-9_]{0,62}",
                str(values[f"{prefix}_DB_{suffix}"]).strip(),
            ):
                raise ActionError("applier database identity is invalid")
    if (
        str(values["PRIMARY_DB_HOST"]).strip().lower() in {"localhost", "127.0.0.1", "::1"}
        and str(values["PRIMARY_DB_PORT"]).strip() == str(values["CRAWL_STAGING_DB_PORT"]).strip()
    ):
        raise ActionError("primary and staging applier databases must be distinct")
    root_cert = Path(str(values["DB_SSLROOTCERT"]).strip())
    if not root_cert.is_absolute() or not root_cert.is_file() or root_cert.is_symlink():
        raise ActionError("applier database root certificate is unavailable")


def _load_service_environment(action: str) -> dict[str, str]:
    import pwd

    expected_user, env_path = ACTION_ACCOUNT_ENV[action]
    actual_user = pwd.getpwuid(os.geteuid()).pw_name
    if actual_user != expected_user:
        raise ActionError("operation was not started under its dedicated service account")
    if not env_path.is_file() or env_path.is_symlink():
        raise ActionError("dedicated service environment is unavailable")

    try:
        parsed = dotenv_values(env_path, interpolate=False)
    except Exception as exc:
        raise ActionError("dedicated service environment could not be parsed") from exc

    values = {
        key: value
        for key, value in parsed.items()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key or "") and value is not None
    }
    missing = [key for key in _required_service_keys(action) if not values.get(key)]
    if missing:
        raise ActionError("dedicated service environment is incomplete")
    _validate_crawler_service_environment(action, values)
    _validate_applier_service_environment(action, values)
    runtime_environment = _trusted_runtime_environment()

    safe_base = {
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": f"{APP_DIR / '.venv' / 'bin'}:/usr/bin:/bin",
        "PYTHONUNBUFFERED": "1",
        "MOONCEN_APP_DIR": str(APP_DIR),
        "MOONCEN_OPS_SERVICE_ACTION": "1",
    }
    sitemap_output = os.environ.get("MOONCEN_SITEMAP_OUTPUT", "")
    os.environ.clear()
    os.environ.update(safe_base)
    os.environ.update(values)
    if runtime_environment:
        os.environ.update(runtime_environment)
    else:
        for key in (
            "TMPDIR",
            "TMP",
            "TEMP",
            "XDG_CACHE_HOME",
            "XDG_CONFIG_HOME",
            "XDG_RUNTIME_DIR",
        ):
            os.environ.pop(key, None)
        os.environ["HOME"] = "/nonexistent"
    os.environ["PYTHONUNBUFFERED"] = "1"
    if sitemap_output:
        os.environ["MOONCEN_SITEMAP_OUTPUT"] = sitemap_output
    return values


def _secret_values(values: dict[str, str]) -> set[str]:
    secret_markers = ("PASSWORD", "SECRET", "TOKEN", "API_KEY", "PRIVATE_KEY", "DATABASE_URL", "DSN")
    return {
        value
        for key, value in values.items()
        if value and len(value) >= 4 and any(marker in key.upper() for marker in secret_markers)
    }


def _redact(text: str, secrets: set[str]) -> str:
    result = text
    for secret in sorted(secrets, key=len, reverse=True):
        variants = {
            secret,
            urllib.parse.quote(secret, safe=""),
            urllib.parse.quote_plus(secret, safe=""),
        }
        for variant in variants:
            if variant:
                result = result.replace(variant, "[REDACTED]")
    result = re.sub(
        r"(?i)([?&](?:key|token|api[_-]?key|access[_-]?token)=)[^&\s]+",
        r"\1[REDACTED]",
        result,
    )
    result = re.sub(
        r"(?i)(postgres(?:ql)?://)[^\s/@:]+(?::[^\s/@]*)?@",
        r"\1[REDACTED]@",
        result,
    )
    return result


def _run_process(
    arguments: list[str],
    secrets: set[str],
    *,
    env_overrides: dict[str, str] | None = None,
) -> int:
    environment = os.environ.copy()
    for key, value in (env_overrides or {}).items():
        if (
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
            or not isinstance(value, str)
            or "\x00" in value
            or len(value.encode("utf-8")) > MAX_CRAWLER_PROVIDER_ENV_BYTES
        ):
            raise ActionError("invalid operation environment override")
        environment[key] = value
    try:
        completed = subprocess.run(
            arguments,
            cwd=APP_DIR,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except Exception as exc:
        print(f"{type(exc).__name__}: operation could not be started", file=sys.stderr)
        return 1
    if completed.stdout:
        sys.stdout.write(_redact(completed.stdout, secrets))
    if completed.stderr:
        sys.stderr.write(_redact(completed.stderr, secrets))
    return completed.returncode


def _run_json_process(
    arguments: list[str],
    secrets: set[str],
) -> tuple[int, dict[str, Any] | None]:
    try:
        completed = subprocess.run(
            arguments,
            cwd=APP_DIR,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except Exception as exc:
        print(f"{type(exc).__name__}: operation could not be started", file=sys.stderr)
        return 1, None
    if completed.returncode != 0:
        if completed.stdout:
            sys.stderr.write(_redact(completed.stdout, secrets))
        if completed.stderr:
            sys.stderr.write(_redact(completed.stderr, secrets))
        return completed.returncode, None
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError:
        print("staging promotion returned invalid JSON", file=sys.stderr)
        return 1, None
    if not isinstance(value, dict):
        print("staging promotion returned a non-object result", file=sys.stderr)
        return 1, None
    if completed.stderr:
        sys.stderr.write(_redact(completed.stderr, secrets))
    return 0, value


def _validate_provider_promotion_result(
    result: dict[str, Any],
    *,
    batch_id: str,
    provider: str,
    dry_run: bool,
) -> None:
    expected_status = "DRY_RUN" if dry_run else "SUCCESS"
    exact_fields = {
        "batch_id": batch_id,
        "dry_run": dry_run,
        "apply_scope_provider": provider,
        "promotion_provider": provider,
        "batch_status": "COLLECTED",
        "failed_owners": [],
        "providers_failed": 0,
        "partial_batch": False,
        "excluded_failed_branches": 0,
        "excluded_failed_courses": 0,
        "collection_complete": False,
        "close_missing_enabled": False,
        "close_requested_providers": [],
        "closed_providers": [],
        "invalid_courses": 0,
        "closed": 0,
        "status": expected_status,
    }
    for field, expected in exact_fields.items():
        if result.get(field) != expected:
            raise ActionError(f"staging promotion gate rejected field: {field}")

    providers = result.get("providers")
    if (
        not isinstance(providers, list)
        or not providers
        or providers != sorted(set(providers))
        or any(not PROVIDER_NAME_PATTERN.fullmatch(str(item)) for item in providers)
        or (provider not in AGGREGATE_PROVIDER_OWNERS and providers != [provider])
    ):
        raise ActionError("staging promotion gate rejected concrete providers")
    if not re.fullmatch(r"[0-9a-f]{64}", str(result.get("staging_fingerprint") or "")):
        raise ActionError("staging promotion gate rejected staging fingerprint")

    courses = result.get("courses")
    valid_courses = result.get("valid_courses")
    inserted = result.get("inserted")
    updated = result.get("updated")
    incoming_counts = result.get("incoming_provider_counts")
    if (
        isinstance(courses, bool)
        or not isinstance(courses, int)
        or courses <= 0
        or valid_courses != courses
        or isinstance(inserted, bool)
        or not isinstance(inserted, int)
        or isinstance(updated, bool)
        or not isinstance(updated, int)
        or inserted < 0
        or updated < 0
        or inserted + updated != courses
        or not isinstance(incoming_counts, dict)
        or set(incoming_counts) != set(providers)
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in incoming_counts.values()
        )
        or sum(incoming_counts.values()) != courses
    ):
        raise ActionError("staging promotion gate rejected course counts")

    scheduled = result.get("scheduled_owners")
    successful = result.get("successful_owners")
    if (
        not isinstance(scheduled, list)
        or scheduled != [provider]
        or successful != scheduled
        or result.get("providers_completed") != 1
    ):
        raise ActionError("staging promotion gate rejected provider ownership")


def _promote_staging_provider(payload: dict[str, Any], secrets: set[str]) -> int:
    batch_id = _clean_token(
        payload.get("batch_id"),
        BATCH_ID_PATTERN.pattern,
        "batch id",
        max_length=128,
    )
    provider = _clean_token(
        payload.get("provider"),
        PROVIDER_NAME_PATTERN.pattern,
        "provider",
        max_length=50,
    )
    python = str(APP_DIR / ".venv" / "bin" / "python")
    base_arguments = [
        python,
        "-X",
        "utf8",
        str(APP_DIR / "tools" / "apply_staging_batch.py"),
        "--batch-id",
        batch_id,
        "--promote-provider",
        provider,
    ]
    dry_code, dry_result = _run_json_process(
        [*base_arguments, "--dry-run"],
        secrets,
    )
    if dry_code != 0 or dry_result is None:
        return dry_code or 1
    _validate_provider_promotion_result(
        dry_result,
        batch_id=batch_id,
        provider=provider,
        dry_run=True,
    )

    staging_fingerprint = str(dry_result["staging_fingerprint"])
    apply_code, apply_result = _run_json_process(
        [
            *base_arguments,
            "--expected-staging-fingerprint",
            staging_fingerprint,
        ],
        secrets,
    )
    if apply_code != 0 or apply_result is None:
        return apply_code or 1
    if apply_result.get("status") == "SKIPPED_ALREADY_APPLIED":
        retry_gate = {
            "batch_id": batch_id,
            "dry_run": False,
            "providers": dry_result["providers"],
            "apply_scope_provider": provider,
            "promotion_provider": provider,
            "staging_fingerprint": staging_fingerprint,
            "successful_apply_fingerprint": staging_fingerprint,
        }
        if any(apply_result.get(key) != value for key, value in retry_gate.items()):
            raise ActionError("staging promotion retry evidence is inconsistent")
        print(
            json.dumps(
                {
                    "status": "ALREADY_PROMOTED",
                    "batch_id": batch_id,
                    "provider": provider,
                    "courses": dry_result["courses"],
                    "closed": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    _validate_provider_promotion_result(
        apply_result,
        batch_id=batch_id,
        provider=provider,
        dry_run=False,
    )
    for field in (
        "staging_fingerprint",
        "branches",
        "courses",
        "valid_courses",
        "incoming_provider_counts",
    ):
        if apply_result.get(field) != dry_result.get(field):
            raise ActionError(f"staging promotion changed after dry-run: {field}")

    print(
        json.dumps(
            {
                "status": "PROMOTED",
                "batch_id": batch_id,
                "provider": provider,
                "courses": apply_result["courses"],
                "inserted": apply_result["inserted"],
                "updated": apply_result["updated"],
                "closed": apply_result["closed"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _clean_token(value: Any, pattern: str, label: str, max_length: int = 100) -> str:
    text = str(value or "").strip()
    if not text or len(text) > max_length or not re.fullmatch(pattern, text):
        raise ActionError(f"invalid {label}")
    return text


def _bounded_int(value: Any, default: int, minimum: int, maximum: int, label: str) -> int:
    try:
        number = int(value if value not in (None, "") else default)
    except (TypeError, ValueError) as exc:
        raise ActionError(f"invalid {label}") from exc
    if not minimum <= number <= maximum:
        raise ActionError(f"invalid {label}")
    return number


def _safe_optional_text(value: Any, label: str, max_length: int = 200) -> str:
    text = str(value or "").strip()
    if len(text) > max_length or any(ord(character) < 32 for character in text):
        raise ActionError(f"invalid {label}")
    return text


def _crawler_provider_execution(provider: str) -> tuple[str, dict[str, str]]:
    """Resolve a concrete provider to one registered crawler owner."""
    app_dir = str(APP_DIR)
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    try:
        from ops_agent.crawler_registry import (
            CrawlerProviderRegistryError,
            resolve_crawler_provider_execution,
        )
    except Exception as exc:
        raise ActionError("crawler provider registry could not be validated") from exc
    try:
        execution = resolve_crawler_provider_execution(provider, APP_DIR)
    except CrawlerProviderRegistryError as exc:
        raise ActionError(str(exc)) from exc
    return execution.scheduled_provider, execution.environment


def _ollama_request(path: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    base_url = str(os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ActionError("Ollama host is invalid")
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="GET" if body is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 - validated operator URL.
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ActionError("Ollama response is too large")
        parsed_body = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed_body, dict):
            raise ActionError("Ollama response is invalid")
        return parsed_body


def _run_ollama_test(secrets: set[str]) -> int:
    model = str(os.environ.get("OLLAMA_MODEL") or "qwen2.5:7b").strip()
    tags = _ollama_request("/api/tags")
    installed = {str(item.get("name") or "") for item in tags.get("models", []) if isinstance(item, dict)}
    if model not in installed:
        print(json.dumps({"ok": False, "model": model, "error": "model_not_installed"}, ensure_ascii=False))
        return 1
    result = _ollama_request(
        "/api/generate",
        payload={"model": model, "prompt": "Reply with OK only.", "stream": False},
    )
    answer = str(result.get("response") or "").strip()
    output = json.dumps({"ok": bool(answer), "model": model, "response": answer}, ensure_ascii=False)
    print(_redact(output, secrets))
    return 0 if answer else 1


def _dispatch_builtin(action: str, secrets: set[str]) -> int | None:
    if action != "ollama-test":
        return None
    return _run_ollama_test(secrets)


def _configured_providers() -> list[str]:
    providers: list[str] = []
    seen: set[str] = set()
    for raw in os.environ.get("CRAWLER_PROVIDERS", "").split():
        provider = raw.strip().upper()
        if re.fullmatch(r"[A-Z0-9_]+", provider) and provider not in seen:
            seen.add(provider)
            providers.append(provider)
    return providers


def _dispatch_internal(action: str) -> int | None:
    if action == "crawler-config":
        print(" ".join(_configured_providers()))
        return 0
    if action not in {"db-summary", "coordinate-summary", "crawler-provider-summary", "replication-summary"}:
        return None

    app_dir = str(APP_DIR)
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    from DB.db_utils import get_db_cursor

    if action == "db-summary":
        with get_db_cursor() as cursor:
            cursor.execute(
                "SELECT current_database() AS name, pg_size_pretty(pg_database_size(current_database())) AS size"
            )
            database = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) AS count, COALESCE(MAX(updated_at)::text, '') AS latest FROM courses")
            courses = cursor.fetchone()
            cursor.execute(
                "SELECT COUNT(*) AS count, "
                "COUNT(*) FILTER (WHERE lat IS NOT NULL AND lon IS NOT NULL) AS with_coordinates FROM branches"
            )
            branches = cursor.fetchone()
        print(f"db\t{database['name']}\t{database['size']}")
        print(f"courses\t{courses['count']}\t{courses['latest']}")
        print(f"branches\t{branches['count']}\t{branches['with_coordinates']}")
        return 0

    if action == "replication-summary":
        with get_db_cursor() as cursor:
            cursor.execute(
                "SELECT CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END AS db_role, "
                "COALESCE(EXTRACT(EPOCH FROM now() - pg_last_xact_replay_timestamp())::int::text, '') "
                "AS replay_delay_seconds"
            )
            row = cursor.fetchone()
        print(f"db_role {row['db_role']}")
        print(f"replay_delay_seconds {row['replay_delay_seconds']}")
        return 0

    if action == "coordinate-summary":
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                SELECT provider,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE address IS NOT NULL AND btrim(address) <> '') AS with_address,
                       COUNT(*) FILTER (WHERE lat IS NOT NULL AND lon IS NOT NULL) AS with_coordinates,
                       COUNT(*) FILTER (WHERE location_verified IS TRUE) AS verified
                FROM branches
                GROUP BY provider
                ORDER BY provider
                """
            )
            rows = cursor.fetchall()
        print("provider\ttotal\twith_address\twith_coordinates\tverified")
        for row in rows:
            print(
                f"{row['provider']}\t{row['total']}\t{row['with_address']}\t"
                f"{row['with_coordinates']}\t{row['verified']}"
            )
        return 0

    providers = _configured_providers()
    if not providers:
        return 0
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            WITH env_config AS (
                SELECT provider, ord
                FROM unnest(%s::text[]) WITH ORDINALITY AS configured(provider, ord)
            )
            SELECT e.provider,
                   COUNT(c.*)::text AS total,
                   COUNT(c.*) FILTER (WHERE c.is_active IS TRUE)::text AS active,
                   COALESCE(MAX(c.updated_at)::text, '') AS latest_updated,
                   COUNT(c.*) FILTER (
                       WHERE c.description IS NOT NULL AND btrim(c.description) <> ''
                   )::text AS with_desc,
                   COUNT(c.*) FILTER (WHERE c.branch_id IS NOT NULL)::text AS with_branch
            FROM env_config e
            LEFT JOIN courses c ON c.provider = e.provider
            GROUP BY e.provider, e.ord
            ORDER BY e.ord
            """,
            [providers],
        )
        rows = cursor.fetchall()
    for row in rows:
        print(
            f"{row['provider']}\t{row['total']}\t{row['active']}\t{row['latest_updated']}\t"
            f"{row['with_desc']}\t{row['with_branch']}"
        )
    return 0


def _dispatch_process(action: str, payload: dict[str, Any], secrets: set[str]) -> int:
    python = str(APP_DIR / ".venv" / "bin" / "python")
    if action == STAGING_PROMOTE_ACTION:
        return _promote_staging_provider(payload, secrets)
    if action in {"ai-reset", "ai-reset-full"}:
        arguments = [python, "-X", "utf8", str(APP_DIR / "run_ai_pipeline.py"), "--reset-ai"]
        if action == "ai-reset-full":
            arguments.append("--reset-target-fields")
        arguments.append("--reset-only")
        return _run_process(arguments, secrets)
    if action == "ai-quality":
        return _run_process(
            [python, "-X", "utf8", str(APP_DIR / "tools" / "ai_quality_report.py"), "--active-only"],
            secrets,
        )
    if action == PRIMARY_LIFECYCLE_ACTION:
        return _run_process(
            [
                python,
                "-X",
                "utf8",
                str(APP_DIR / "tools" / "cleanup_ended_courses.py"),
            ],
            secrets,
        )
    if action == "coordinate-backfill":
        try:
            delay = float(os.environ.get("CRAWLER_COORDINATE_BACKFILL_DELAY") or "0.5")
            confidence = int(os.environ.get("CRAWLER_LOCATION_MIN_CONFIDENCE") or "75")
            raw_limit = os.environ.get("CRAWLER_COORDINATE_BACKFILL_LIMIT", "").strip()
            limit = int(raw_limit) if raw_limit else 0
        except ValueError as exc:
            raise ActionError("invalid coordinate backfill service setting") from exc
        if not 0 <= delay <= 60 or not 0 <= confidence <= 100 or not 0 <= limit <= 100000:
            raise ActionError("invalid coordinate backfill service setting")
        arguments = [
            python,
            "-X",
            "utf8",
            str(APP_DIR / "tools" / "maintenance" / "kakao_geocode_branches.py"),
            "--with-active-courses",
            "--address-only",
            "--retry-after-days",
            "30",
            "--delay",
            str(delay),
            "--min-confidence",
            str(confidence),
        ]
        if limit:
            arguments.extend(["--limit", str(limit)])
        return _run_process(arguments, secrets)
    if action == "crawler-provider":
        provider = _clean_token(
            payload.get("provider"),
            PROVIDER_NAME_PATTERN.pattern,
            "provider",
            max_length=50,
        )
        branch_code = _safe_optional_text(payload.get("branch_code"), "branch code")
        branch_name = _safe_optional_text(payload.get("branch_name"), "branch name")
        full = payload.get("full", False)
        if not isinstance(full, bool):
            raise ActionError("full must be a boolean")
        if full and payload.get("limit") not in (None, ""):
            raise ActionError("full runs cannot declare a limit")
        if full and (branch_code or branch_name):
            raise ActionError("full runs cannot use a branch filter")
        limit = (
            None
            if full
            else _bounded_int(
                payload.get("limit"),
                DEFAULT_CRAWLER_PROVIDER_LIMIT,
                1,
                MAX_CRAWLER_PROVIDER_LIMIT,
                "limit",
            )
        )
        if (branch_code or branch_name) and provider not in BRANCH_FILTER_PROVIDERS:
            raise ActionError("provider does not support branch-filtered runs")
        scheduled_provider, env_overrides = _crawler_provider_execution(provider)
        arguments = [
            python,
            "-X",
            "utf8",
            str(APP_DIR / "run_crawlers.py"),
            "--providers",
            scheduled_provider,
            "--once",
            "--ignore-active-window",
            "--skip-coordinate-backfill",
            "--skip-category-backfill",
        ]
        if limit is not None:
            arguments.extend(["--limit", str(limit)])
        if branch_code:
            arguments.extend(["--branch-code", branch_code])
        if branch_name:
            arguments.extend(["--branch-name", branch_name])
        return _run_process(arguments, secrets, env_overrides=env_overrides)
    if action == "sitemap":
        output = Path(os.environ.get("MOONCEN_SITEMAP_OUTPUT", ""))
        if not output.is_absolute() or output.name != "sitemap.xml":
            raise ActionError("fixed sitemap output was not provided")
        site_url = os.environ.get("SITE_URL") or os.environ.get("VITE_SITE_URL")
        if not site_url:
            raise ActionError("site URL is missing from the crawler service environment")
        return _run_process(
            [
                python,
                "-X",
                "utf8",
                str(APP_DIR / "tools" / "generate_frontend_sitemap.py"),
                "--site-url",
                site_url,
                "--output",
                str(output),
            ],
            secrets,
        )
    raise ActionError("unsupported operation")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ACTION_ACCOUNT_ENV:
        print("mooncen service action: unsupported operation", file=sys.stderr)
        return 64
    action = sys.argv[1]
    try:
        values = _load_service_environment(action)
        secrets = _secret_values(values)
        payload = _read_payload() if action in PAYLOAD_ACTIONS else {}
        builtin_result = _dispatch_builtin(action, secrets)
        if builtin_result is not None:
            return builtin_result
        internal_result = _dispatch_internal(action)
        if internal_result is not None:
            return internal_result
        return _dispatch_process(action, payload, secrets)
    except ActionError as exc:
        print(str(exc), file=sys.stderr)
        return 64
    except Exception as exc:
        print(f"{type(exc).__name__}: operation failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
