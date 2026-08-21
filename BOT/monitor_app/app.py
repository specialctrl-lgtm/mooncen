import json
import math
import os
import secrets
import stat
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from flask import Flask, Response, jsonify, request


APP_HOST = os.environ.get("MONITOR_APP_HOST", "0.0.0.0")
APP_PORT = int(os.environ.get("MONITOR_APP_PORT", "8088"))
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090").rstrip("/")
APP_TOKEN = os.environ.get("MONITOR_APP_TOKEN", "")
OPERATION_ENABLED = os.environ.get("MONITOR_APP_OPERATION_ENABLED", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
OPERATION_TOKEN = os.environ.get("MONITOR_APP_OPERATION_TOKEN", "")
MOONCEN_OPS_API_BASE_URL = os.environ.get("MOONCEN_OPS_API_BASE_URL", "https://mooncen.kr/api/ops").rstrip("/")
MOONCEN_PUBLIC_BASE_URL = os.environ.get(
    "MOONCEN_PUBLIC_BASE_URL",
    "https://mooncen.kr",
).rstrip("/")
MOONCEN_SERVER_MONITOR_BASE_URL = os.environ.get(
    "MOONCEN_SERVER_MONITOR_BASE_URL",
    "https://mooncen.kr",
).strip().rstrip("/")
MOONCEN_SERVER_MONITOR_TOKEN = os.environ.get(
    "MOONCEN_SERVER_MONITOR_TOKEN",
    "",
).strip()
MOONCEN_SERVER_MONITOR_QUALITY_PATH = "/api/monitoring/crawler-quality"
COMMAND_TIMEOUT_SECONDS = int(os.environ.get("MONITOR_APP_COMMAND_TIMEOUT_SECONDS", "30"))
TAILSCALE_STATUS_FILE = (
    os.environ.get("MONITOR_APP_TAILSCALE_SNAPSHOT_FILE", "").strip()
    or os.environ.get("TAILSCALE_STATUS_FILE", "").strip()
    or "/var/lib/mooncen-monitor/tailscale-status.json"
)
_TAILSCALE_MAX_AGE_VALUE = (
    os.environ.get("MONITOR_APP_TAILSCALE_SNAPSHOT_MAX_AGE_SECONDS", "").strip()
    or os.environ.get("TAILSCALE_STATUS_MAX_AGE_SECONDS", "").strip()
    or "180"
)
try:
    TAILSCALE_STATUS_MAX_AGE_SECONDS = max(
        30,
        min(3600, int(_TAILSCALE_MAX_AGE_VALUE)),
    )
except ValueError:
    TAILSCALE_STATUS_MAX_AGE_SECONDS = 180
TAILSCALE_STATUS_MAX_BYTES = 1024 * 1024
POSIX_SNAPSHOT_SECURITY = os.name == "posix"


def bounded_env_number(name, default, minimum, maximum, number_type=int):
    try:
        value = number_type(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def configured_core_node(value, fallback):
    node = str(value or "").strip().lower()
    if (
        not node
        or len(node) > 64
        or not node[0].isalnum()
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for character in node)
    ):
        return fallback
    return node


CORE_SERVICE_ORDER = ("database", "frontend", "backend", "crawler")
CORE_STATUS_VALUES = ("healthy", "warning", "critical", "unknown")
CORE_SERVICE_LABELS = {
    "database": "DB",
    "frontend": "FRONT",
    "backend": "BACKEND",
    "crawler": "CRAWLER",
}
CORE_PRIMARY_NODE = configured_core_node(
    os.environ.get("MONITOR_APP_PRIMARY_NODE", "cloud"),
    "cloud",
)
CORE_CRAWLER_RUNTIME_NODE = configured_core_node(
    os.environ.get(
        "MONITOR_APP_CRAWLER_RUNTIME_NODE",
        os.environ.get("MONITOR_APP_CRAWLER_NODE", "cloud"),
    ),
    "cloud",
)
CORE_CRAWLER_TARGET_NODE = configured_core_node(
    os.environ.get("MONITOR_APP_CRAWLER_TARGET_NODE", "gen1crawler"),
    "gen1crawler",
)
CORE_CRAWLER_CONTROL_NODE = configured_core_node(
    os.environ.get("MONITOR_APP_CRAWLER_CONTROL_NODE", "gen1db"),
    "gen1db",
)
CORE_CRAWLER_MODE = "legacy"
CORE_FUNCTIONAL_TIMEOUT_SECONDS = bounded_env_number(
    "MONITOR_APP_FUNCTIONAL_TIMEOUT_SECONDS",
    5.0,
    1.0,
    15.0,
    float,
)
MOONCEN_SERVER_MONITOR_TIMEOUT_SECONDS = bounded_env_number(
    "MOONCEN_SERVER_MONITOR_TIMEOUT_SECONDS",
    20.0,
    1.0,
    30.0,
    float,
)
CORE_CRAWLER_MAX_AGE_SECONDS = bounded_env_number(
    "MONITOR_APP_CRAWLER_MAX_AGE_SECONDS",
    129600,
    3600,
    604800,
)
CORE_CRAWLER_TRIGGER_GRACE_SECONDS = 300
CRAWLER_MONITORING_PROVIDER_LIMIT = 20
CRAWLER_MONITORING_MAX_ERRORS = 8
CRAWLER_MONITORING_ERROR_LENGTH = 180
CRAWLER_MONITORING_MAX_COUNT = 1_000_000_000_000
CRAWLER_QUALITY_SCHEMA_VERSION = 1
CRAWLER_QUALITY_MAX_ISSUE_STATUSES = 100
CRAWLER_QUALITY_CACHE_TTL_SECONDS = 240.0
CRAWLER_QUALITY_FAILURE_TTL_SECONDS = 30.0
CRAWLER_QUALITY_INITIAL_WAIT_SECONDS = 2.0
CRAWLER_QUALITY_COUNT_FIELDS = (
    "active_courses",
    "missing_required",
    "invalid_dates",
    "invalid_prices",
    "missing_address",
    "missing_coordinates",
    "incomplete_location",
    "out_of_korea",
    "duplicate_urls",
    "blocked_sync",
)
TEMPERATURE_MAX_AGE_SECONDS = 300
CORE_CACHE_TTL_SECONDS = bounded_env_number(
    "MONITOR_APP_CORE_CACHE_TTL_SECONDS",
    30,
    5,
    300,
)
_CORE_SNAPSHOT_CACHE = {"expires_at": 0.0, "value": None}
_CORE_SNAPSHOT_LOCK = threading.Lock()
_CRAWLER_QUALITY_CACHE = {
    "generation": 0,
    "expires_at": 0.0,
    "value": None,
    "refreshing": False,
    "event": None,
}
_CRAWLER_QUALITY_CACHE_LOCK = threading.Lock()


def parse_excluded_nodes(value):
    return frozenset(
        item.strip().casefold().rstrip(".")
        for item in str(value or "").split(",")
        if item.strip()
    )


DEFAULT_EXCLUDED_NODES = frozenset({"ds1515", "ds718", "n100"})


def configured_excluded_nodes(value):
    return DEFAULT_EXCLUDED_NODES | parse_excluded_nodes(value)


EXCLUDED_NODES = configured_excluded_nodes(
    os.environ.get("MONITOR_APP_EXCLUDED_NODES", "")
)
MOONCEN_BACKUP_NODE = "cloud"
MOONCEN_BACKUP_TIMER = "mooncen-backup.timer"
SEOUL_TIMEZONE = ZoneInfo("Asia/Seoul")
TRUSTED_HOSTS = [
    item.strip()
    for item in os.environ.get("MONITOR_APP_TRUSTED_HOSTS", "").split(",")
    if item.strip()
]

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
if TRUSTED_HOSTS:
    app.config["TRUSTED_HOSTS"] = TRUSTED_HOSTS


def node_is_excluded(value):
    """Match configured node names case-insensitively, including Tailscale FQDNs."""
    normalized = str(value or "").strip().casefold().rstrip(".")
    if not normalized:
        return False
    if normalized.startswith("[") and "]" in normalized:
        normalized = normalized[1:normalized.index("]")]
    elif normalized.count(":") == 1:
        host, port = normalized.rsplit(":", 1)
        if port.isdigit():
            normalized = host
    short_name = normalized.split(".", 1)[0]
    return normalized in EXCLUDED_NODES or short_name in EXCLUDED_NODES


def labels_reference_excluded_node(labels):
    labels = labels or {}
    if node_is_excluded(labels.get("node")):
        return True
    instance = str(labels.get("instance") or "").strip()
    if instance.startswith("[") and "]" in instance:
        instance_host = instance[1:instance.index("]")]
    else:
        instance_host = instance.rsplit(":", 1)[0] if ":" in instance else instance
    return node_is_excluded(instance_host)


def tailscale_unavailable(error):
    return {
        "available": False,
        "status": "unavailable",
        "stale": True,
        "generated_at": None,
        "age_seconds": None,
        "backend_state": "Unknown",
        "counts": {"total": 0, "online": 0, "offline": 0},
        "summary": {
            "total": 0,
            "online": 0,
            "offline": 0,
            "direct": 0,
            "relay": 0,
        },
        "self": None,
        "peers": [],
        "error": error,
    }


def snapshot_text(value, max_length):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > max_length:
        return ""
    if any(ord(character) < 32 for character in value):
        return ""
    return value


def snapshot_timestamp(value):
    value = snapshot_text(value, 64)
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.year < 2000:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_tailscale_node(value):
    if not isinstance(value, dict):
        return None
    name = snapshot_text(value.get("name"), 253)
    dns_name = snapshot_text(value.get("dns_name"), 253)
    if not name or node_is_excluded(name) or node_is_excluded(dns_name):
        return None
    online = value.get("online") is True
    active = value.get("active") is True
    connection = snapshot_text(value.get("connection"), 16)
    if not online:
        connection = "offline"
    elif not active:
        connection = "idle"
    elif connection not in {"direct", "relay"}:
        connection = "unknown"
    return {
        "name": name,
        "dns_name": dns_name,
        "os": snapshot_text(value.get("os"), 64) or "unknown",
        "online": online,
        "active": active,
        "connection": connection,
        "last_seen": snapshot_timestamp(value.get("last_seen")),
        "key_expiry": snapshot_timestamp(value.get("key_expiry")),
    }


def read_tailscale_snapshot_file(path):
    if not path or not os.path.isabs(path):
        raise ValueError("snapshot path must be absolute")
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("snapshot is not a regular file")
        if metadata.st_size <= 0 or metadata.st_size > TAILSCALE_STATUS_MAX_BYTES:
            raise ValueError("snapshot has an invalid size")
        if POSIX_SNAPSHOT_SECURITY:
            if metadata.st_uid != 0:
                raise PermissionError("snapshot is not owned by root")
            unsafe_mode = stat.S_IWGRP | stat.S_IXGRP | stat.S_IRWXO
            if metadata.st_mode & unsafe_mode:
                raise PermissionError("snapshot permissions are too broad")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            payload = stream.read(TAILSCALE_STATUS_MAX_BYTES + 1)
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(payload) > TAILSCALE_STATUS_MAX_BYTES:
        raise ValueError("snapshot exceeds the size limit")
    return json.loads(payload.decode("utf-8"))


def normalize_tailscale_snapshot(raw_snapshot, *, now=None, max_age_seconds=None):
    if not isinstance(raw_snapshot, dict) or raw_snapshot.get("schema_version") != 1:
        raise ValueError("unsupported snapshot schema")
    generated_at = snapshot_timestamp(raw_snapshot.get("generated_at"))
    if not generated_at:
        raise ValueError("snapshot has no valid generation time")

    raw_peers = raw_snapshot.get("peers")
    if not isinstance(raw_peers, list) or len(raw_peers) > 2048:
        raise ValueError("snapshot peer list is invalid")
    peers_by_name = {}
    for raw_peer in raw_peers:
        peer = normalize_tailscale_node(raw_peer)
        if peer:
            peers_by_name[peer["name"].casefold()] = peer
    peers = sorted(peers_by_name.values(), key=lambda item: item["name"].casefold())
    online_count = sum(1 for peer in peers if peer["online"])
    direct_count = sum(1 for peer in peers if peer["connection"] == "direct")
    relay_count = sum(1 for peer in peers if peer["connection"] == "relay")

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    generated_time = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    raw_age = (current_time.astimezone(timezone.utc) - generated_time).total_seconds()
    maximum_age = (
        TAILSCALE_STATUS_MAX_AGE_SECONDS
        if max_age_seconds is None
        else max(1, int(max_age_seconds))
    )
    stale = raw_age < -60 or raw_age > maximum_age

    return {
        "available": True,
        "status": "stale" if stale else "current",
        "stale": stale,
        "generated_at": generated_at,
        "age_seconds": max(0, int(raw_age)),
        "backend_state": snapshot_text(raw_snapshot.get("backend_state"), 64) or "Unknown",
        "counts": {
            "total": len(peers),
            "online": online_count,
            "offline": len(peers) - online_count,
        },
        "summary": {
            "total": len(peers),
            "online": online_count,
            "offline": len(peers) - online_count,
            "direct": direct_count,
            "relay": relay_count,
        },
        "self": normalize_tailscale_node(raw_snapshot.get("self")),
        "peers": peers,
        "error": "snapshot_stale" if stale else None,
    }


def get_tailscale_status(*, now=None):
    try:
        raw_snapshot = read_tailscale_snapshot_file(TAILSCALE_STATUS_FILE)
        return normalize_tailscale_snapshot(raw_snapshot, now=now)
    except FileNotFoundError:
        return tailscale_unavailable("snapshot_missing")
    except PermissionError:
        return tailscale_unavailable("snapshot_unreadable")
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return tailscale_unavailable("snapshot_invalid")


def require_token():
    if not APP_TOKEN:
        return jsonify({"error": "service token is not configured"}), 503
    provided = request.headers.get("X-App-Token")
    if secrets.compare_digest(str(provided or ""), APP_TOKEN):
        return None
    return jsonify({"error": "unauthorized"}), 401


@app.before_request
def auth_api_requests():
    if request.path.startswith("/api/"):
        return require_token()
    return None


@app.after_request
def secure_api_response(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


def prometheus_get(path, params=None, timeout=4):
    resp = requests.get(f"{PROMETHEUS_URL}{path}", params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(data.get("error") or "Prometheus query failed")
    return data.get("data", {})


def query_vector(query):
    return prometheus_get("/api/v1/query", {"query": query}).get("result", [])


def query_range(query, start, end, step):
    return prometheus_get(
        "/api/v1/query_range",
        {"query": query, "start": start, "end": end, "step": step},
        timeout=8,
    ).get("result", [])


def label_value(row, name, default="-"):
    return row.get("metric", {}).get(name, default) or default


def prometheus_label(value):
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def prometheus_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def format_summary_number(value, suffix="", decimals=1):
    if value is None:
        return "-"
    try:
        return f"{float(value):.{decimals}f}{suffix}"
    except Exception:
        return "-"


def format_temperature_celsius(value, *, node_up):
    """Format only plausible, finite temperature evidence from an online node."""
    if not node_up or value is None or isinstance(value, bool):
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return "-"
    if not math.isfinite(number) or number < -20 or number > 130:
        return "-"
    return format_summary_number(number, "C", 0)


def format_uptime(seconds):
    if seconds is None:
        return "-"
    try:
        seconds = int(float(seconds))
    except Exception:
        return "-"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    if days:
        return f"{days}d {hours}h"
    minutes = (seconds % 3600) // 60
    return f"{hours}h {minutes}m"


def timestamp_seconds(value):
    if not value:
        return 0.0
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        try:
            return parsedate_to_datetime(str(value)).timestamp()
        except Exception:
            return 0.0


def ops_get(path, timeout=8):
    resp = requests.get(f"{MOONCEN_OPS_API_BASE_URL}{path}", timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def operation_token_valid():
    if not OPERATION_ENABLED or not OPERATION_TOKEN:
        return False
    provided = request.headers.get("X-Operation-Token")
    return secrets.compare_digest(str(provided or ""), OPERATION_TOKEN)


def temperature_promql(node_selector):
    """Return fresh, successful temperature evidence grouped by topology node."""
    selector = str(node_selector or "").strip()
    if not selector or any(character in selector for character in "{}\n\r"):
        raise ValueError("invalid temperature node selector")
    custom = (
        f'(max by (node) (mooncen_hardware_temperature_celsius'
        f'{{job="windows_exporter",{selector}}}) '
        f'and on (node) (max by (node) (mooncen_temperature_collector_success'
        f'{{job="windows_exporter",{selector}}}) == 1) '
        f'and on (node) (max by (node) (mooncen_temperature_sensor_count'
        f'{{job="windows_exporter",{selector}}}) > 0) '
        f'and on (node) ((time() - max by (node) '
        f'(mooncen_temperature_collector_timestamp_seconds'
        f'{{job="windows_exporter",{selector}}})) >= -60) '
        f'and on (node) ((time() - max by (node) '
        f'(mooncen_temperature_collector_timestamp_seconds'
        f'{{job="windows_exporter",{selector}}})) <= {TEMPERATURE_MAX_AGE_SECONDS}))'
    )
    temperatures = (
        f'{custom} '
        f'or max by (node) (node_hwmon_temp_celsius'
        f'{{job="node_exporter",{selector}}}) '
        f'or max by (node) (node_thermal_zone_temp'
        f'{{job="node_exporter",{selector}}}) '
        f'or max by (node) (node_thermal_temperature_celsius'
        f'{{job="node_exporter",{selector}}})'
    )
    return (
        f'({temperatures}) and on (node) '
        f'(max by (node) (up{{job=~"node_exporter|windows_exporter",{selector}}}) == 1)'
    )


def get_server_rows():
    nodes = {}
    for item in query_vector('up{node!="",job=~"node_exporter|windows_exporter"}'):
        metric = item.get("metric", {})
        node = metric.get("node")
        if not node or node_is_excluded(node):
            continue
        nodes[node] = {
            "node": node,
            "up": "UP" if str(item.get("value", [None, "0"])[1]) == "1" else "DOWN",
            "role": metric.get("role", ""),
            "alerting": metric.get("alerting", ""),
        }

    queries = {
        "cpu": '100 - (avg by (node) (rate(node_cpu_seconds_total{mode="idle",node!=""}[5m])) * 100) or 100 - (avg by (node) (rate(windows_cpu_time_total{mode="idle",node!=""}[5m])) * 100)',
        "mem": '(1 - (node_memory_MemAvailable_bytes{node!=""} / node_memory_MemTotal_bytes{node!=""})) * 100 or (1 - ((node_memory_free_bytes{node!=""} + node_memory_inactive_bytes{node!=""}) / node_memory_total_bytes{node!=""})) * 100 or (1 - (windows_memory_available_bytes{node!=""} / windows_memory_physical_total_bytes{node!=""})) * 100',
        "disk": '100 - ((node_filesystem_avail_bytes{mountpoint="/",fstype!="rootfs",node!=""} * 100) / node_filesystem_size_bytes{mountpoint="/",fstype!="rootfs",node!=""}) or 100 - ((windows_logical_disk_free_bytes{volume="C:",node!=""} * 100) / windows_logical_disk_size_bytes{volume="C:",node!=""})',
        "temp": temperature_promql('node!=""'),
        "uptime": 'time() - node_boot_time_seconds{node!=""} or time() - windows_system_boot_time_timestamp{node!=""}',
    }
    value_tasks = {
        key: (lambda query=query: query_values_by_node(query), {})
        for key, query in queries.items()
    }
    values, _ = collect_parallel(value_tasks)

    rows = []
    for node in sorted(nodes):
        item = nodes[node]
        rows.append({
            "node": node,
            "up": item["up"],
            "cpu": format_summary_number(values["cpu"].get(node), "%"),
            "mem": format_summary_number(values["mem"].get(node), "%"),
            "disk": format_summary_number(values["disk"].get(node), "%"),
            "temp": format_temperature_celsius(
                values["temp"].get(node), node_up=item["up"] == "UP"
            ),
            "uptime": format_uptime(values["uptime"].get(node)),
            "role": item["role"],
            "alerting": item["alerting"],
        })
    return rows


def get_scrape_targets():
    rows = []
    data = prometheus_get("/api/v1/targets", {"state": "any"}, timeout=4)
    for target in data.get("activeTargets", []):
        labels = target.get("labels", {})
        discovered = target.get("discoveredLabels", {})
        health = target.get("health") or "unknown"
        node = labels.get("node") or discovered.get("node") or labels.get("instance") or "-"
        if node_is_excluded(node):
            continue
        rows.append(
            {
                "node": node,
                "job": labels.get("job") or discovered.get("job") or "-",
                "instance": labels.get("instance") or discovered.get("__address__") or "-",
                "health": health,
                "role": labels.get("role") or discovered.get("role") or "-",
                "alerting": labels.get("alerting") or discovered.get("alerting") or "-",
                "last_error": target.get("lastError") or "",
            }
        )
    return sorted(rows, key=lambda item: (item["health"] != "up", item["node"], item["job"]))


def first_value(query):
    rows = query_vector(query)
    if not rows:
        return None
    try:
        return float(rows[0].get("value", [0, 0])[1])
    except Exception:
        return None


def get_core_topology():
    crawler_runtime_drift = CORE_CRAWLER_RUNTIME_NODE != CORE_CRAWLER_TARGET_NODE
    service_nodes = {
        "database": CORE_PRIMARY_NODE,
        "frontend": CORE_PRIMARY_NODE,
        "backend": CORE_PRIMARY_NODE,
        # This compatibility field is the current checked runtime owner, not
        # the reviewed post-cutover worker placement.
        "crawler": CORE_CRAWLER_RUNTIME_NODE,
    }
    return {
        "environment": "production",
        "active_node": CORE_PRIMARY_NODE,
        "crawler_mode": CORE_CRAWLER_MODE,
        "crawler_runtime_node": CORE_CRAWLER_RUNTIME_NODE,
        "crawler_target_node": CORE_CRAWLER_TARGET_NODE,
        "crawler_control_node": CORE_CRAWLER_CONTROL_NODE,
        "crawler_transition_state": (
            "cutover_pending" if crawler_runtime_drift else "target_runtime"
        ),
        "crawler_runtime_drift": crawler_runtime_drift,
        "service_nodes": service_nodes,
    }


def sample_float(item):
    try:
        return float(item.get("value", [None, None])[1])
    except (TypeError, ValueError, IndexError):
        return None


def nullable_first_value(query):
    try:
        return first_value(query)
    except Exception:
        return None


def get_core_runtime_status(topology):
    service_nodes = topology["service_nodes"]
    specs = {
        "database": (
            service_nodes["database"],
            "postgresql.service",
        ),
        "frontend": (
            service_nodes["frontend"],
            "mooncen-frontend.service",
        ),
        "backend": (
            service_nodes["backend"],
            "mooncen-api.service",
        ),
    }
    tasks = {
        service: (
            lambda node=node, unit=unit: nullable_first_value(
                f'node_systemd_unit_state{{node="{node}",name="{unit}",state="active"}}'
            ),
            None,
        )
        for service, (node, unit) in specs.items()
    }
    crawler_node = service_nodes["crawler"]
    tasks["crawler"] = (
        lambda: nullable_first_value(
            f'node_systemd_unit_state{{node="{crawler_node}",'
            'name="mooncen-crawler.timer",state="active"}'
        ),
        None,
    )
    values, _errors = collect_parallel(tasks)
    result = {}
    for service in CORE_SERVICE_ORDER:
        value = values.get(service)
        runtime_ok = None if value is None else value == 1
        primary_node = service_nodes[service]
        result[service] = {
            "runtime_ok": runtime_ok,
            "active_nodes": [primary_node] if runtime_ok else [],
            "value": value,
        }
    return result


def get_primary_status(topology):
    expected_node = topology["active_node"]
    try:
        recovery_rows = query_vector("mooncen_postgres_in_recovery")
    except Exception:
        recovery_rows = None
    try:
        role_rows = query_vector("mooncen_node_role")
    except Exception:
        role_rows = None

    recovery_values = {}
    if recovery_rows is not None:
        for item in recovery_rows:
            node = item.get("metric", {}).get("node")
            value = sample_float(item)
            if node and value in (0.0, 1.0):
                recovery_values[node] = value

    candidates = []
    role_data_available = bool(role_rows)
    if role_rows:
        for item in role_rows:
            metric = item.get("metric", {})
            node = metric.get("node")
            exported_role = metric.get("exported_role") or metric.get("role")
            if node and exported_role == "primary" and sample_float(item) == 1:
                candidates.append(node)
    candidates = sorted(set(candidates))
    observed_node = candidates[0] if len(candidates) == 1 else None
    role_ok = None if not role_data_available else candidates == [expected_node]
    database_writable = (
        None
        if observed_node is None or observed_node not in recovery_values
        else recovery_values[observed_node] == 0
    )
    matches_topology = None if observed_node is None else observed_node == expected_node
    if not role_data_available:
        status = "unknown"
    elif len(candidates) != 1 or matches_topology is False or database_writable is False:
        status = "critical"
    elif database_writable is None:
        status = "unknown"
    else:
        status = "healthy"
    return {
        "node": observed_node,
        "expected_node": expected_node,
        "status": status,
        "ok": status == "healthy",
        "role_ok": role_ok,
        "database_writable": database_writable,
        "candidates": candidates,
        "matches_topology": matches_topology,
    }


def public_service_get(path):
    last_error = None
    for attempt in range(2):
        try:
            response = requests.get(
                f"{MOONCEN_PUBLIC_BASE_URL}{path}",
                headers={
                    "Accept": "application/json, text/html;q=0.9",
                    "User-Agent": "MoonCen-Monitor/1.0",
                },
                timeout=CORE_FUNCTIONAL_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt == 0:
                continue
            raise
        if response.status_code >= 500 and attempt == 0:
            continue
        return response
    if last_error is not None:
        raise last_error
    return response


def probe_frontend():
    try:
        response = public_service_get("/")
    except requests.RequestException:
        return {"ok": False, "detail": "공개 FRONT에 연결할 수 없습니다."}
    if response.status_code != 200:
        return {"ok": False, "detail": f"FRONT HTTP {response.status_code}"}
    if '<div id="root"' not in response.text:
        return {"ok": False, "detail": "FRONT root marker가 없습니다."}
    return {"ok": True, "detail": "공개 FRONT root가 정상 응답했습니다."}


def probe_public_health():
    try:
        response = public_service_get("/health")
    except requests.RequestException:
        return {"ok": False, "detail": "공개 BACKEND health에 연결할 수 없습니다."}
    if response.status_code != 200:
        return {"ok": False, "detail": f"BACKEND health HTTP {response.status_code}"}
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return {"ok": False, "detail": "BACKEND health JSON이 올바르지 않습니다."}
    if not isinstance(payload, dict) or payload.get("status") != "ready":
        return {"ok": False, "detail": "BACKEND 또는 DB가 ready 상태가 아닙니다."}
    return {"ok": True, "detail": "BACKEND와 DB readiness가 정상입니다."}


def probe_course_list():
    try:
        response = public_service_get("/api/courses/?size=1")
    except requests.RequestException:
        return {"ok": False, "detail": "공개 강좌 API에 연결할 수 없습니다."}
    if response.status_code != 200:
        return {"ok": False, "detail": f"강좌 API HTTP {response.status_code}"}
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return {"ok": False, "detail": "강좌 API JSON이 올바르지 않습니다."}
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        return {"ok": False, "detail": "강좌 API가 유효한 항목을 반환하지 않았습니다."}
    return {"ok": True, "detail": "공개 강좌 API가 실제 데이터를 반환했습니다."}


def crawler_trigger_without_completion(values, reference_timestamp, current):
    try:
        timer_last_trigger = float(values.get("timer_last_trigger"))
        one_shot_active = float(values.get("one_shot_active"))
        reference_timestamp = float(reference_timestamp)
        current = float(current)
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(value) for value in (
        timer_last_trigger,
        one_shot_active,
        reference_timestamp,
        current,
    )):
        return False
    return (
        timer_last_trigger > reference_timestamp
        and current - timer_last_trigger >= CORE_CRAWLER_TRIGGER_GRACE_SECONDS
        and one_shot_active == 0
    )


def get_crawler_functional_status(node, now=None):
    queries = {
        "state_valid": f'mooncen_crawler_cycle_state_valid{{node="{node}"}}',
        "bad_outcome": (
            "max("
            f'mooncen_crawler_cycle_outcome{{node="{node}",outcome=~"failed|partial_success|zero_provider"}}'
            ")"
        ),
        "requested": f'mooncen_crawler_cycle_providers_requested{{node="{node}"}}',
        "failed": f'mooncen_crawler_cycle_providers_failed{{node="{node}"}}',
        "last_completion": (
            f'mooncen_crawler_cycle_last_completion_timestamp_seconds{{node="{node}"}}'
        ),
        "legacy_last_success": (
            f'mooncen_crawler_last_success_timestamp_seconds{{node="{node}"}}'
        ),
        "timer_last_trigger": (
            f'node_systemd_timer_last_trigger_seconds{{node="{node}",'
            'name="mooncen-crawler.timer"}'
        ),
        "one_shot_active": (
            f'node_systemd_unit_state{{node="{node}",'
            'name="mooncen-crawler-once.service",state="active"}'
        ),
        "unit_failed": (
            f'mooncen_systemd_unit_result_failed{{node="{node}",unit="mooncen-crawler-once.service"}}'
        ),
    }
    values, _errors = collect_parallel({
        name: (lambda query=query: nullable_first_value(query), None)
        for name, query in queries.items()
    })
    required_names = ("state_valid", "bad_outcome", "requested", "failed", "last_completion")
    if any(values.get(name) is None for name in required_names):
        last_success = values.get("legacy_last_success")
        if last_success is not None:
            current = float(now if now is not None else time.time())
            age_seconds = current - float(last_success)
            fresh = (
                float(last_success) > 0
                and -300 <= age_seconds <= CORE_CRAWLER_MAX_AGE_SECONDS
            )
            if not fresh:
                return {
                    "ok": False,
                    "status": "critical",
                    "detail": "CRAWLER 마지막 성공 시각이 허용 범위를 벗어났습니다.",
                }
            if crawler_trigger_without_completion(values, last_success, current):
                return {
                    "ok": False,
                    "status": "warning",
                    "detail": (
                        "CRAWLER timer가 마지막 성공 이후 실행됐지만 "
                        "완료되지 않았습니다."
                    ),
                }
            degraded = (
                values.get("unit_failed") == 1
                or values.get("state_valid") not in (None, 1)
                or values.get("bad_outcome") not in (None, 0)
                or (
                    values.get("requested") is not None
                    and values["requested"] <= 0
                )
                or (
                    values.get("failed") is not None
                    and values["failed"] > 0
                )
            )
            if degraded:
                return {
                    "ok": False,
                    "status": "warning",
                    "detail": "CRAWLER 최근 성공은 확인됐지만 실패 증거가 함께 있습니다.",
                }
            completed_at = datetime.fromtimestamp(
                float(last_success),
                timezone.utc,
            ).isoformat()
            return {
                "ok": True,
                "status": "healthy",
                "detail": f"CRAWLER 최근 정상 완료: {completed_at}",
            }
        return {
            "ok": None,
            "status": "unknown",
            "detail": "CRAWLER 최근 실행 증거가 충분하지 않습니다.",
        }

    current = float(now if now is not None else time.time())
    last_completion = float(values["last_completion"])
    age_seconds = current - last_completion
    fresh = last_completion > 0 and -300 <= age_seconds <= CORE_CRAWLER_MAX_AGE_SECONDS
    unit_failed = values.get("unit_failed")
    degraded = (
        values["state_valid"] != 1
        or values["bad_outcome"] != 0
        or values["requested"] <= 0
        or values["failed"] > 0
        or unit_failed == 1
    )
    if not fresh:
        status = "critical"
        ok = False
        detail = "CRAWLER 최근 완료 시각이 허용 범위를 벗어났습니다."
    elif crawler_trigger_without_completion(values, last_completion, current):
        status = "warning"
        ok = False
        detail = "CRAWLER timer 실행 이후 완료 증거가 없습니다."
    elif degraded:
        status = "warning"
        ok = False
        detail = "CRAWLER 최근 실행이 실패·부분 성공 또는 빈 결과로 끝났습니다."
    else:
        status = "healthy"
        ok = True
        completed_at = datetime.fromtimestamp(last_completion, timezone.utc).isoformat()
        detail = f"CRAWLER 최근 정상 완료: {completed_at}"
    return {"ok": ok, "status": status, "detail": detail}


def core_service_row(service, topology, runtime, functional, checked_at):
    functional_ok = functional.get("ok")
    runtime_ok = runtime.get("runtime_ok")
    explicit_status = functional.get("status")
    if explicit_status in ("warning", "critical", "unknown"):
        status = explicit_status
    elif functional_ok is False:
        status = "critical"
    elif functional_ok is None:
        status = "unknown"
    elif runtime_ok is True:
        status = "healthy"
    else:
        status = "warning"
    primary_node = topology["service_nodes"][service]
    return {
        "service": service,
        "label": CORE_SERVICE_LABELS[service],
        "node": primary_node,
        "primary_node": primary_node,
        "active_nodes": runtime.get("active_nodes", []),
        "runtime_ok": runtime_ok,
        "functional_ok": functional_ok,
        "ok": status == "healthy",
        "status": status,
        "detail": functional.get("detail") or "상태 증거가 없습니다.",
        "checked_at": checked_at,
    }


def core_snapshot_payload(topology, primary, core_services, generated_at):
    problems = build_problem_list(core_services, primary)
    primary_status = str((primary or {}).get("status") or "unknown")
    critical_count = sum(1 for row in core_services if row["status"] == "critical")
    critical_count += int(primary_status == "critical")
    warning_count = sum(
        1
        for row in core_services
        if row["status"] in ("warning", "unknown")
    )
    warning_count += int(primary_status in ("warning", "unknown"))
    overall_status = (
        "critical"
        if critical_count
        else "warning"
        if warning_count
        else "healthy"
    )
    counts = {
        "core_services": len(core_services),
        "healthy_services": sum(1 for row in core_services if row["status"] == "healthy"),
        "failing_services": sum(1 for row in core_services if row["status"] == "critical"),
        "warning_services": sum(1 for row in core_services if row["status"] == "warning"),
        "unknown_services": sum(1 for row in core_services if row["status"] == "unknown"),
        "critical": critical_count,
        "warning": warning_count,
    }
    return {
        "generated_at": generated_at,
        "status": overall_status,
        "status_label": {
            "healthy": "정상",
            "warning": "주의",
            "critical": "장애",
        }[overall_status],
        "topology": topology,
        "primary": primary,
        "core_services": core_services,
        "services": core_services,
        "counts": counts,
        "problems": problems,
    }


def collect_core_snapshot():
    topology = get_core_topology()
    checked_at = datetime.now(timezone.utc).isoformat()
    crawler_node = topology["service_nodes"]["crawler"]
    collected, _errors = collect_parallel({
        "runtime": (lambda: get_core_runtime_status(topology), {}),
        "primary": (lambda: get_primary_status(topology), None),
        "frontend": (probe_frontend, {"ok": None, "detail": "FRONT probe 오류"}),
        "health": (probe_public_health, {"ok": None, "detail": "health probe 오류"}),
        "courses": (probe_course_list, {"ok": None, "detail": "강좌 API probe 오류"}),
        "crawler": (
            lambda: get_crawler_functional_status(crawler_node),
            {"ok": None, "detail": "CRAWLER probe 오류"},
        ),
    })
    runtime = collected["runtime"]
    primary = collected["primary"] or {
        "node": None,
        "expected_node": topology["active_node"],
        "status": "unknown",
        "ok": False,
        "role_ok": None,
        "database_writable": None,
        "candidates": [],
        "matches_topology": None,
    }
    health = collected["health"]
    database_ok = health.get("ok")
    if primary.get("database_writable") is False:
        database_ok = False
    database = {
        "ok": database_ok,
        "detail": (
            health.get("detail")
            if primary.get("database_writable") is not False
            else "선언된 Primary DB가 writable 상태가 아닙니다."
        ),
    }
    course_probe = collected["courses"]
    if health.get("ok") is False or course_probe.get("ok") is False:
        backend_ok = False
    elif health.get("ok") is None or course_probe.get("ok") is None:
        backend_ok = None
    else:
        backend_ok = True
    backend = {
        "ok": backend_ok,
        "detail": (
            course_probe.get("detail")
            if health.get("ok") is True
            else health.get("detail")
        ),
    }
    crawler = dict(collected["crawler"])
    crawler_runtime = runtime.get("crawler", {}).get("runtime_ok")
    if crawler_runtime is False:
        crawler.update({
            "ok": False,
            "status": "critical",
            "detail": "CRAWLER 자동 실행 timer가 활성 상태가 아닙니다.",
        })

    functional = {
        "database": database,
        "frontend": collected["frontend"],
        "backend": backend,
        "crawler": crawler,
    }
    core_services = [
        core_service_row(
            service,
            topology,
            runtime.get(service, {}),
            functional[service],
            checked_at,
        )
        for service in CORE_SERVICE_ORDER
    ]
    return core_snapshot_payload(topology, primary, core_services, checked_at)


def unknown_core_snapshot():
    topology = get_core_topology()
    generated_at = datetime.now(timezone.utc).isoformat()
    primary = {
        "node": None,
        "expected_node": topology["active_node"],
        "status": "unknown",
        "ok": False,
        "role_ok": None,
        "database_writable": None,
        "candidates": [],
        "matches_topology": None,
    }
    core_services = [
        core_service_row(
            service,
            topology,
            {"runtime_ok": None, "active_nodes": []},
            {"ok": None, "detail": "핵심 상태 수집에 실패했습니다."},
            generated_at,
        )
        for service in CORE_SERVICE_ORDER
    ]
    return core_snapshot_payload(topology, primary, core_services, generated_at)


def clear_core_snapshot_cache():
    with _CORE_SNAPSHOT_LOCK:
        _CORE_SNAPSHOT_CACHE["expires_at"] = 0.0
        _CORE_SNAPSHOT_CACHE["value"] = None


def get_cached_core_snapshot(force=False):
    now = time.monotonic()
    with _CORE_SNAPSHOT_LOCK:
        cached = _CORE_SNAPSHOT_CACHE["value"]
        if not force and cached is not None and now < _CORE_SNAPSHOT_CACHE["expires_at"]:
            return cached
    try:
        snapshot = collect_core_snapshot()
    except Exception:
        snapshot = unknown_core_snapshot()
    with _CORE_SNAPSHOT_LOCK:
        _CORE_SNAPSHOT_CACHE["value"] = snapshot
        _CORE_SNAPSHOT_CACHE["expires_at"] = time.monotonic() + CORE_CACHE_TTL_SECONDS
    return snapshot


def get_service_checks():
    """Compatibility alias for clients and metrics that still consume services."""
    return get_cached_core_snapshot()["core_services"]


def get_ops_health():
    try:
        return ops_get("/health")
    except Exception as exc:
        return {"ok": False, "error": str(exc), "tables": {}}


def get_ops_crawler_summary():
    try:
        return ops_get(
            "/crawlers/analytics"
            f"?environment=production&window_hours=24"
            f"&provider_limit={CRAWLER_MONITORING_PROVIDER_LIMIT}"
            "&worker_limit=3&correlation_limit=1"
        )
    except Exception as exc:
        return {"ok": False, "available": False, "summary_24h": [], "latest_failures": [], "error": str(exc)}


def get_ops_quality_summary():
    try:
        return ops_get("/course-quality-summary")
    except Exception as exc:
        return {"ok": False, "available": False, "grades": [], "providers": [], "missing_fields": [], "error": str(exc)}


def crawler_monitoring_error(section, code, detail=None):
    error = {
        "section": snapshot_text(section, 32) or "crawler",
        "code": snapshot_text(code, 64) or "unavailable",
    }
    safe_detail = snapshot_text(str(detail or ""), CRAWLER_MONITORING_ERROR_LENGTH)
    if safe_detail:
        error["detail"] = safe_detail
    return error


def nullable_nonnegative_number(value, *, integer=False, minimum=0, maximum=None):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        not math.isfinite(number)
        or number < minimum
        or (maximum is not None and number > maximum)
    ):
        return None
    if integer:
        if maximum is None:
            maximum = CRAWLER_MONITORING_MAX_COUNT
            if number > maximum:
                return None
        if not number.is_integer():
            return None
        return int(number)
    return number


def crawler_quality_unavailable(reason_code):
    reason = snapshot_text(reason_code, 64)
    if (
        not reason
        or not reason[0].islower()
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in reason)
    ):
        reason = "server_monitor_response_invalid"
    return {
        "schema_version": CRAWLER_QUALITY_SCHEMA_VERSION,
        "generated_at": None,
        "available": False,
        "reason_code": reason,
        "source": None,
        "counts": {field: None for field in CRAWLER_QUALITY_COUNT_FIELDS},
        "issue_statuses": [],
        "latest_scan_at": None,
        "rule_source": None,
    }


def strict_crawler_quality_count(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > CRAWLER_MONITORING_MAX_COUNT:
        return None
    return value


def strict_crawler_quality_code(value, max_length=64):
    code = snapshot_text(value, max_length)
    if (
        not code
        or not code[0].islower()
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in code)
    ):
        return None
    return code


def strict_crawler_quality_label(value, max_length=64):
    label = snapshot_text(value, max_length)
    if (
        not label
        or not label[0].islower()
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_-"
            for character in label
        )
    ):
        return None
    return label


def normalize_crawler_quality_snapshot(raw):
    if not isinstance(raw, dict):
        return crawler_quality_unavailable("server_monitor_response_invalid")
    raw_schema_version = raw.get("schema_version")
    if (
        isinstance(raw_schema_version, bool)
        or raw_schema_version != CRAWLER_QUALITY_SCHEMA_VERSION
    ):
        return crawler_quality_unavailable("server_monitor_response_invalid")
    raw_available = raw.get("available")
    if raw_available is not True:
        if raw_available is not False:
            return crawler_quality_unavailable("server_monitor_response_invalid")
        return crawler_quality_unavailable(
            strict_crawler_quality_code(raw.get("reason_code"))
            or "production_quality_unavailable"
        )
    generated_at = snapshot_timestamp(raw.get("generated_at"))
    if generated_at is None or raw.get("source") != "production_database":
        return crawler_quality_unavailable("server_monitor_response_invalid")
    raw_counts = raw.get("counts")
    if not isinstance(raw_counts, dict):
        return crawler_quality_unavailable("server_monitor_response_invalid")
    counts = {
        field: strict_crawler_quality_count(raw_counts.get(field))
        for field in CRAWLER_QUALITY_COUNT_FIELDS
    }
    if any(value is None for value in counts.values()):
        return crawler_quality_unavailable("server_monitor_response_invalid")
    raw_issue_statuses = raw.get("issue_statuses")
    if (
        not isinstance(raw_issue_statuses, list)
        or len(raw_issue_statuses) > CRAWLER_QUALITY_MAX_ISSUE_STATUSES
    ):
        return crawler_quality_unavailable("server_monitor_response_invalid")
    issue_statuses = []
    seen_issue_statuses = set()
    for row in raw_issue_statuses:
        if not isinstance(row, dict):
            return crawler_quality_unavailable("server_monitor_response_invalid")
        status = strict_crawler_quality_label(row.get("status"))
        severity = strict_crawler_quality_label(row.get("severity"))
        issue_count = strict_crawler_quality_count(row.get("issue_count"))
        key = (status, severity)
        if (
            status is None
            or severity is None
            or issue_count is None
            or key in seen_issue_statuses
        ):
            return crawler_quality_unavailable("server_monitor_response_invalid")
        seen_issue_statuses.add(key)
        issue_statuses.append({
            "status": status,
            "severity": severity,
            "issue_count": issue_count,
        })
    raw_latest_scan_at = raw.get("latest_scan_at")
    latest_scan_at = snapshot_timestamp(raw_latest_scan_at)
    if raw_latest_scan_at is not None and latest_scan_at is None:
        return crawler_quality_unavailable("server_monitor_response_invalid")
    rule_source = snapshot_text(raw.get("rule_source"), 160)
    if not rule_source:
        return crawler_quality_unavailable("server_monitor_response_invalid")
    return {
        "schema_version": CRAWLER_QUALITY_SCHEMA_VERSION,
        "generated_at": generated_at,
        "available": True,
        "reason_code": None,
        "source": "production_database",
        "counts": counts,
        "issue_statuses": issue_statuses,
        "latest_scan_at": latest_scan_at,
        "rule_source": rule_source,
    }


def server_monitor_quality_url():
    try:
        parsed = urlsplit(MOONCEN_SERVER_MONITOR_BASE_URL)
        hostname = parsed.hostname
        parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        return None
    return (
        f"https://{parsed.netloc.rstrip('/')}"
        f"{MOONCEN_SERVER_MONITOR_QUALITY_PATH}"
    )


def crawler_quality_snapshot():
    if not MOONCEN_SERVER_MONITOR_TOKEN:
        return crawler_quality_unavailable("server_monitor_token_not_configured")
    if (
        len(MOONCEN_SERVER_MONITOR_TOKEN) < 32
        or len(MOONCEN_SERVER_MONITOR_TOKEN) > 256
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for character in MOONCEN_SERVER_MONITOR_TOKEN
        )
    ):
        return crawler_quality_unavailable("server_monitor_token_invalid")
    url = server_monitor_quality_url()
    if url is None:
        return crawler_quality_unavailable("server_monitor_base_url_invalid")
    try:
        response = requests.get(
            url,
            headers={
                "X-MoonCen-Monitor-Token": MOONCEN_SERVER_MONITOR_TOKEN,
            },
            timeout=MOONCEN_SERVER_MONITOR_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        if not 200 <= response.status_code < 300:
            return crawler_quality_unavailable("server_monitor_request_failed")
        raw = response.json()
    except (requests.RequestException, ValueError, TypeError):
        return crawler_quality_unavailable("server_monitor_request_failed")
    return normalize_crawler_quality_snapshot(raw)


def clear_crawler_quality_cache():
    with _CRAWLER_QUALITY_CACHE_LOCK:
        _CRAWLER_QUALITY_CACHE["generation"] += 1
        _CRAWLER_QUALITY_CACHE["expires_at"] = 0.0
        _CRAWLER_QUALITY_CACHE["value"] = None
        _CRAWLER_QUALITY_CACHE["refreshing"] = False
        _CRAWLER_QUALITY_CACHE["event"] = None


def refresh_crawler_quality_cache(generation, refresh_event):
    try:
        value = crawler_quality_snapshot()
    except Exception:
        value = crawler_quality_unavailable("collector_unavailable")
    ttl = (
        CRAWLER_QUALITY_CACHE_TTL_SECONDS
        if value.get("available") is True
        else CRAWLER_QUALITY_FAILURE_TTL_SECONDS
    )
    with _CRAWLER_QUALITY_CACHE_LOCK:
        if (
            generation == _CRAWLER_QUALITY_CACHE["generation"]
            and refresh_event is _CRAWLER_QUALITY_CACHE["event"]
        ):
            _CRAWLER_QUALITY_CACHE["value"] = value
            _CRAWLER_QUALITY_CACHE["expires_at"] = time.monotonic() + ttl
            _CRAWLER_QUALITY_CACHE["refreshing"] = False
    refresh_event.set()


def get_cached_crawler_quality_snapshot():
    current = time.monotonic()
    start_refresh = False
    with _CRAWLER_QUALITY_CACHE_LOCK:
        cached = _CRAWLER_QUALITY_CACHE["value"]
        if cached is not None and current < _CRAWLER_QUALITY_CACHE["expires_at"]:
            return cached
        if not _CRAWLER_QUALITY_CACHE["refreshing"]:
            _CRAWLER_QUALITY_CACHE["generation"] += 1
            generation = _CRAWLER_QUALITY_CACHE["generation"]
            refresh_event = threading.Event()
            _CRAWLER_QUALITY_CACHE["refreshing"] = True
            _CRAWLER_QUALITY_CACHE["event"] = refresh_event
            start_refresh = True
        else:
            generation = _CRAWLER_QUALITY_CACHE["generation"]
            refresh_event = _CRAWLER_QUALITY_CACHE["event"]
    if start_refresh:
        try:
            threading.Thread(
                target=refresh_crawler_quality_cache,
                args=(generation, refresh_event),
                name="mooncen-crawler-quality-refresh",
                daemon=True,
            ).start()
        except RuntimeError:
            failure = crawler_quality_unavailable("quality_refresh_start_failed")
            with _CRAWLER_QUALITY_CACHE_LOCK:
                if (
                    generation == _CRAWLER_QUALITY_CACHE["generation"]
                    and refresh_event is _CRAWLER_QUALITY_CACHE["event"]
                ):
                    _CRAWLER_QUALITY_CACHE["value"] = failure
                    _CRAWLER_QUALITY_CACHE["expires_at"] = (
                        time.monotonic() + CRAWLER_QUALITY_FAILURE_TTL_SECONDS
                    )
                    _CRAWLER_QUALITY_CACHE["refreshing"] = False
            refresh_event.set()
    if cached is not None:
        return cached
    refresh_event.wait(CRAWLER_QUALITY_INITIAL_WAIT_SECONDS)
    with _CRAWLER_QUALITY_CACHE_LOCK:
        refreshed = _CRAWLER_QUALITY_CACHE["value"]
        if refreshed is not None:
            return refreshed
    return crawler_quality_unavailable("quality_refresh_pending")


def crawler_latest_snapshot(node, now=None):
    current = float(now if now is not None else time.time())
    queries = {
        "state_valid": f'mooncen_crawler_cycle_state_valid{{node="{node}"}}',
        "completed_at": (
            f'mooncen_crawler_cycle_last_completion_timestamp_seconds{{node="{node}"}}'
        ),
        "last_success_at": (
            f'mooncen_crawler_last_success_timestamp_seconds{{node="{node}"}}'
        ),
        "timer_last_trigger": (
            f'node_systemd_timer_last_trigger_seconds{{node="{node}",'
            'name="mooncen-crawler.timer"}'
        ),
        "running": (
            'max('
            f'node_systemd_unit_state{{node="{node}",'
            'name="mooncen-crawler-once.service",state=~"active|activating"}'
            ')'
        ),
        "unit_failed": (
            f'mooncen_systemd_unit_result_failed{{node="{node}",'
            'unit="mooncen-crawler-once.service"}'
        ),
        "providers_requested": (
            f'mooncen_crawler_cycle_providers_requested{{node="{node}"}}'
        ),
        "providers_succeeded": (
            f'mooncen_crawler_cycle_providers_completed{{node="{node}"}}'
        ),
        "providers_failed": f'mooncen_crawler_cycle_providers_failed{{node="{node}"}}',
    }
    tasks = {
        name: (lambda query=query: first_value(query), None)
        for name, query in queries.items()
    }
    for outcome in ("success", "partial_success", "failed", "zero_provider", "running"):
        tasks[f"outcome:{outcome}"] = (
            lambda outcome=outcome: first_value(
                f'mooncen_crawler_cycle_outcome{{node="{node}",outcome="{outcome}"}}'
            ),
            None,
        )
    values, collector_errors = collect_parallel(tasks)
    state_valid = nullable_nonnegative_number(values.get("state_valid"), integer=True)
    running_value = nullable_nonnegative_number(values.get("running"), integer=True)
    running = running_value == 1
    completed_epoch = nullable_nonnegative_number(values.get("completed_at"))
    success_epoch = nullable_nonnegative_number(values.get("last_success_at"))
    timer_epoch = nullable_nonnegative_number(values.get("timer_last_trigger"))

    def epoch_timestamp(epoch):
        if epoch is None or epoch <= 0 or epoch > current + 300:
            return None
        try:
            return datetime.fromtimestamp(epoch, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None

    completed_at = epoch_timestamp(completed_epoch)
    last_success_at = epoch_timestamp(success_epoch)
    started_at = epoch_timestamp(timer_epoch) if running else None
    last_success_age_seconds = (
        max(0.0, current - success_epoch)
        if last_success_at is not None and success_epoch is not None
        else None
    )
    active_outcomes = [
        outcome
        for outcome in ("success", "partial_success", "failed", "zero_provider", "running")
        if nullable_nonnegative_number(values.get(f"outcome:{outcome}")) == 1
    ]
    if running:
        status = "running"
    elif nullable_nonnegative_number(values.get("unit_failed"), integer=True) == 1:
        status = "failed"
    elif state_valid == 1 and len(active_outcomes) == 1:
        status = active_outcomes[0]
    elif last_success_at is not None:
        status = "success"
    else:
        status = "unknown"
    available = status != "unknown" and (
        running or completed_at is not None or last_success_at is not None
    ) and (running_value is not None or state_valid == 1)
    errors = []
    if collector_errors:
        errors.append(crawler_monitoring_error("latest", "prometheus_query_unavailable"))
    if state_valid != 1 and last_success_at is None:
        errors.append(crawler_monitoring_error("latest", "durable_cycle_state_unavailable"))
    if not running and completed_at is None and last_success_at is None:
        errors.append(crawler_monitoring_error("latest", "completion_timestamp_unavailable"))
    if status == "unknown":
        errors.append(crawler_monitoring_error("latest", "cycle_outcome_unavailable"))
    return {
        "available": available,
        "source": (
            "prometheus_durable_cycle_metrics"
            if state_valid == 1
            else "prometheus_legacy_runtime_metrics"
        ),
        "started_at": started_at,
        "completed_at": completed_at or (None if running else last_success_at),
        "last_success_at": last_success_at,
        "last_success_age_seconds": last_success_age_seconds,
        "running": running,
        "status": status,
        "duration_seconds": (
            max(0.0, current - timer_epoch)
            if running and started_at is not None and timer_epoch is not None
            else None
        ),
        "providers_requested": nullable_nonnegative_number(
            values.get("providers_requested"), integer=True
        ),
        "providers_succeeded": nullable_nonnegative_number(
            values.get("providers_succeeded"), integer=True
        ),
        "providers_failed": nullable_nonnegative_number(
            values.get("providers_failed"), integer=True
        ),
        "collected_count": None,
        "new_count": None,
        "updated_count": None,
        "skipped_count": None,
    }, errors


def crawler_node_snapshots(topology):
    placements = (
        ("runtime", topology["crawler_runtime_node"]),
        ("target", topology["crawler_target_node"]),
        ("control", topology["crawler_control_node"]),
    )
    node_names = sorted({node for _role, node in placements})
    node_pattern = "|".join(node_names)
    queries = {
        "up": f'up{{node=~"{node_pattern}",job=~"node_exporter|windows_exporter"}}',
        "cpu": (
            f'100 - (avg by (node) (rate(node_cpu_seconds_total{{mode="idle",node=~"{node_pattern}"}}[5m])) * 100) '
            f'or 100 - (avg by (node) (rate(windows_cpu_time_total{{mode="idle",node=~"{node_pattern}"}}[5m])) * 100)'
        ),
        "memory": (
            f'(1 - (node_memory_MemAvailable_bytes{{node=~"{node_pattern}"}} '
            f'/ node_memory_MemTotal_bytes{{node=~"{node_pattern}"}})) * 100 '
            f'or (1 - (windows_memory_available_bytes{{node=~"{node_pattern}"}} '
            f'/ windows_memory_physical_total_bytes{{node=~"{node_pattern}"}})) * 100'
        ),
        "disk": (
            f'100 - ((node_filesystem_avail_bytes{{node=~"{node_pattern}",'
            'mountpoint="/",fstype!="rootfs"} * 100) '
            f'/ node_filesystem_size_bytes{{node=~"{node_pattern}",'
            'mountpoint="/",fstype!="rootfs"}) '
            f'or 100 - ((windows_logical_disk_free_bytes{{node=~"{node_pattern}",'
            'volume="C:"} * 100) '
            f'/ windows_logical_disk_size_bytes{{node=~"{node_pattern}",volume="C:"}})'
        ),
        "load": f'node_load1{{node=~"{node_pattern}"}}',
        "logical_cpu_count": (
            f'count by (node) (node_cpu_seconds_total{{node=~"{node_pattern}",mode="idle"}}) '
            f'or count by (node) (windows_cpu_time_total{{node=~"{node_pattern}",mode="idle"}})'
        ),
        "temperature": temperature_promql(f'node=~"{node_pattern}"'),
    }
    values, collector_errors = collect_parallel({
        name: (lambda query=query: query_values_by_node(query), {})
        for name, query in queries.items()
    })
    up_values = values.get("up") or {}
    cpu_values = values.get("cpu") or {}
    memory_values = values.get("memory") or {}
    disk_values = values.get("disk") or {}
    load_values = values.get("load") or {}
    cpu_count_values = values.get("logical_cpu_count") or {}
    temperatures = values.get("temperature") or {}
    rows = []
    for role, node in placements:
        raw_up = nullable_nonnegative_number(up_values.get(node))
        status = "up" if raw_up == 1 else "down" if raw_up == 0 else "unknown"
        temperature = nullable_nonnegative_number(
            temperatures.get(node), minimum=-20, maximum=130
        )
        cpu_percent = nullable_nonnegative_number(cpu_values.get(node), maximum=100)
        memory_percent = nullable_nonnegative_number(memory_values.get(node), maximum=100)
        disk_percent = nullable_nonnegative_number(disk_values.get(node), maximum=100)
        load_1m = nullable_nonnegative_number(load_values.get(node), maximum=100_000)
        logical_cpu_count = nullable_nonnegative_number(
            cpu_count_values.get(node), integer=True, minimum=1, maximum=4096
        )
        node_error = None if status != "unknown" else "node availability evidence unavailable"
        node_available = any(
            value is not None
            for value in (
                raw_up,
                temperature,
                cpu_percent,
                memory_percent,
                disk_percent,
                load_1m,
                logical_cpu_count,
            )
        )
        rows.append({
            "node": node,
            "role": role,
            "available": node_available,
            "status": status,
            "temperature_available": temperature is not None,
            "temp_celsius": temperature,
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "load_1m": load_1m,
            "disk_percent": disk_percent,
            "logical_cpu_count": logical_cpu_count,
            "error": node_error,
        })
    errors = []
    if collector_errors:
        errors.append(crawler_monitoring_error("nodes", "prometheus_query_unavailable"))
    return rows, errors


def crawler_ops_snapshot():
    raw = get_ops_crawler_summary()
    if not isinstance(raw, dict) or raw.get("available") is False:
        return (
            {
                "available": False,
                "has_data": None,
                "reasons": [{"code": "ops_crawler_summary_unavailable"}],
                "source": "ops_crawler_summary",
                "window_hours": 24,
                "run_count": None,
                "success_count": None,
                "partial_count": None,
                "failure_count": None,
                "in_progress_count": None,
                "collected_count": None,
                "processed_count": None,
                "new_count": None,
                "updated_count": None,
                "skipped_count": None,
                "avg_duration_seconds": None,
                "last_run_at": None,
            },
            {
                "available": False,
                "has_data": None,
                "reasons": [{"code": "ops_crawler_summary_unavailable"}],
                "total": None,
                "limit": CRAWLER_MONITORING_PROVIDER_LIMIT,
                "truncated": None,
                "items": [],
            },
            [crawler_monitoring_error("statistics", "ops_crawler_summary_unavailable")],
        )

    # The legacy endpoint returned summary_24h rows. The current central API
    # returns the bounded crawler analytics sections used below.
    if not isinstance(raw.get("collection"), dict):
        return (
            {
                "available": False,
                "has_data": None,
                "reasons": [{"code": "ops_crawler_contract_unavailable"}],
                "source": "ops_crawler_summary",
                "window_hours": 24,
                "run_count": None,
                "success_count": None,
                "partial_count": None,
                "failure_count": None,
                "in_progress_count": None,
                "collected_count": None,
                "processed_count": None,
                "new_count": None,
                "updated_count": None,
                "skipped_count": None,
                "avg_duration_seconds": None,
                "last_run_at": None,
            },
            {
                "available": False,
                "has_data": None,
                "reasons": [{"code": "ops_crawler_contract_unavailable"}],
                "total": None,
                "limit": CRAWLER_MONITORING_PROVIDER_LIMIT,
                "truncated": None,
                "items": [],
            },
            [crawler_monitoring_error("statistics", "ops_crawler_contract_unavailable")],
        )

    collection = raw.get("collection") if isinstance(raw.get("collection"), dict) else {}
    runs = (
        collection.get("components", {}).get("runs", {})
        if isinstance(collection.get("components"), dict)
        else {}
    )
    totals = runs.get("totals") if isinstance(runs.get("totals"), dict) else {}
    runs_available = runs.get("available") is True
    run_count = nullable_nonnegative_number(totals.get("run_count"), integer=True)
    summary = {
        "available": runs_available,
        "has_data": runs.get("has_data") if runs_available else None,
        "reasons": [] if runs_available else [{"code": "collection_statistics_unavailable"}],
        "source": "crawler_control_database",
        "window_hours": 24,
        "run_count": run_count,
        "success_count": nullable_nonnegative_number(totals.get("successful_runs"), integer=True),
        "partial_count": nullable_nonnegative_number(totals.get("partial_runs"), integer=True),
        "failure_count": nullable_nonnegative_number(totals.get("failed_runs"), integer=True),
        "in_progress_count": nullable_nonnegative_number(
            totals.get("in_progress_runs"), integer=True
        ),
        "collected_count": nullable_nonnegative_number(
            totals.get("collected_count"), integer=True
        ),
        "processed_count": nullable_nonnegative_number(
            totals.get("processed_count"), integer=True
        ),
        "new_count": nullable_nonnegative_number(totals.get("new_count"), integer=True),
        "updated_count": nullable_nonnegative_number(
            totals.get("updated_count"), integer=True
        ),
        # The central analytics contract has no immutable skipped aggregate yet.
        "skipped_count": None,
        "avg_duration_seconds": None,
        "last_run_at": snapshot_timestamp(totals.get("last_run_at")),
    }
    provider_section = raw.get("providers") if isinstance(raw.get("providers"), dict) else {}
    provider_component = (
        provider_section.get("components", {}).get("collection", {})
        if isinstance(provider_section.get("components"), dict)
        else {}
    )
    provider_available = provider_component.get("available") is True
    raw_items = provider_component.get("items") if isinstance(provider_component.get("items"), list) else []
    items = []
    for row in raw_items[:CRAWLER_MONITORING_PROVIDER_LIMIT]:
        if not isinstance(row, dict):
            continue
        provider = snapshot_text(row.get("provider"), 96)
        if not provider:
            continue
        items.append({
            "provider": provider,
            "run_count": nullable_nonnegative_number(row.get("run_count"), integer=True),
            "success_count": nullable_nonnegative_number(row.get("successful_runs"), integer=True),
            "partial_count": nullable_nonnegative_number(row.get("partial_runs"), integer=True),
            "failure_count": nullable_nonnegative_number(row.get("failed_runs"), integer=True),
            "collected_count": nullable_nonnegative_number(
                row.get("collected_count"), integer=True
            ),
            "new_count": nullable_nonnegative_number(row.get("new_count"), integer=True),
            "updated_count": nullable_nonnegative_number(row.get("updated_count"), integer=True),
            "failed_item_count": nullable_nonnegative_number(
                row.get("failed_item_count"), integer=True
            ),
            "success_rate": nullable_nonnegative_number(
                row.get("success_rate"), maximum=100
            ),
            "last_run_at": snapshot_timestamp(row.get("last_run_at")),
        })
    total = nullable_nonnegative_number(provider_component.get("total"), integer=True)
    providers = {
        "available": provider_available,
        "has_data": provider_component.get("has_data") if provider_available else None,
        "reasons": [] if provider_available else [{"code": "provider_statistics_unavailable"}],
        "total": total,
        "limit": CRAWLER_MONITORING_PROVIDER_LIMIT,
        "truncated": (total > len(items)) if total is not None else None,
        "items": items,
    }
    errors = []
    if not runs_available:
        errors.append(crawler_monitoring_error("summary_24h", "collection_statistics_unavailable"))
    if not provider_available:
        errors.append(crawler_monitoring_error("providers", "provider_statistics_unavailable"))
    return summary, providers, errors


def collect_crawler_monitoring_snapshot():
    core = get_cached_core_snapshot()
    topology = dict(core.get("topology") or get_core_topology())
    crawler_rows = [
        row
        for row in core.get("core_services", [])
        if isinstance(row, dict) and row.get("service") == "crawler"
    ]
    crawler = crawler_rows[0] if len(crawler_rows) == 1 else {}
    runtime_node = topology.get("crawler_runtime_node")
    if not isinstance(runtime_node, str) or not runtime_node:
        runtime_node = CORE_CRAWLER_RUNTIME_NODE
    collected, collector_errors = collect_parallel({
        "latest": (lambda: crawler_latest_snapshot(runtime_node), ({
            "available": False,
            "source": "prometheus_durable_cycle_metrics",
            "started_at": None,
            "completed_at": None,
            "last_success_at": None,
            "last_success_age_seconds": None,
            "running": False,
            "status": "unknown",
            "duration_seconds": None,
            "providers_requested": None,
            "providers_succeeded": None,
            "providers_failed": None,
            "collected_count": None,
            "new_count": None,
            "updated_count": None,
            "skipped_count": None,
        }, [crawler_monitoring_error("latest", "collector_unavailable")])),
        "operations": (crawler_ops_snapshot, ({
            "available": False,
            "has_data": None,
            "reasons": [{"code": "collector_unavailable"}],
            "source": "ops_crawler_summary",
            "window_hours": 24,
            "run_count": None,
            "success_count": None,
            "partial_count": None,
            "failure_count": None,
            "in_progress_count": None,
            "collected_count": None,
            "processed_count": None,
            "new_count": None,
            "updated_count": None,
            "skipped_count": None,
            "avg_duration_seconds": None,
            "last_run_at": None,
        }, {
            "available": False,
            "has_data": None,
            "reasons": [{"code": "collector_unavailable"}],
            "total": None,
            "limit": CRAWLER_MONITORING_PROVIDER_LIMIT,
            "truncated": None,
            "items": [],
        }, [crawler_monitoring_error("statistics", "collector_unavailable")])),
        "nodes": (lambda: crawler_node_snapshots(topology), (
            [], [crawler_monitoring_error("nodes", "collector_unavailable")]
        )),
        "quality": (
            get_cached_crawler_quality_snapshot,
            crawler_quality_unavailable("collector_unavailable"),
        ),
    })
    latest, latest_errors = collected["latest"]
    summary_24h, providers, ops_errors = collected["operations"]
    nodes, node_errors = collected["nodes"]
    quality = collected["quality"]
    errors = [
        *latest_errors,
        *ops_errors,
        *node_errors,
        *(
            [crawler_monitoring_error("crawler", "collector_unavailable")]
            if collector_errors
            else []
        ),
    ][:CRAWLER_MONITORING_MAX_ERRORS]
    core_status = crawler.get("status")
    status = core_status if core_status in CORE_STATUS_VALUES else "unknown"
    node_data_available = any(row.get("available") for row in nodes)
    available = bool(
        latest.get("available") or summary_24h.get("available") or node_data_available
    )
    complete = bool(
        latest.get("available")
        and summary_24h.get("available")
        and providers.get("available")
        and nodes
        and all(row.get("available") for row in nodes)
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "available": available,
        "complete": complete,
        "partial": available and not complete,
        "status": status,
        "topology": topology,
        "latest": latest,
        "summary_24h": summary_24h,
        "providers": providers,
        "nodes": nodes,
        "quality": quality,
        "errors": errors,
    }


def backup_freshness_cutoff(now=None):
    """Return yesterday 00:00 KST, the oldest acceptable scheduled backup."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current_kst = current.astimezone(SEOUL_TIMEZONE)
    today_kst = current_kst.replace(hour=0, minute=0, second=0, microsecond=0)
    return today_kst - timedelta(days=1)


def backup_status(now=None):
    """Report only MoonCen's cloud backup timer, using its last trigger freshness."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    query = (
        'node_systemd_timer_last_trigger_seconds'
        f'{{node="{MOONCEN_BACKUP_NODE}",name="{MOONCEN_BACKUP_TIMER}"}}'
    )
    last_trigger = None
    for item in query_vector(query):
        metric = item.get("metric", {})
        if (
            metric.get("node") != MOONCEN_BACKUP_NODE
            or metric.get("name") != MOONCEN_BACKUP_TIMER
        ):
            continue
        try:
            candidate = float(item.get("value", [None, None])[1])
        except (TypeError, ValueError, IndexError):
            continue
        if not math.isfinite(candidate) or candidate <= 0:
            continue
        if last_trigger is None or candidate > last_trigger:
            last_trigger = candidate
    if last_trigger is None:
        return []

    triggered_at = datetime.fromtimestamp(last_trigger, tz=timezone.utc)
    cutoff_kst = backup_freshness_cutoff(current)
    # A future timestamp is not evidence of a usable backup.
    timestamp_valid = triggered_at <= current + timedelta(minutes=5)
    fresh = timestamp_valid and triggered_at >= cutoff_kst.astimezone(timezone.utc)
    return [{
        "node": MOONCEN_BACKUP_NODE,
        "name": MOONCEN_BACKUP_TIMER,
        # Kept for older clients; this now represents freshness, not oneshot state.
        "active": fresh,
        "fresh": fresh,
        "fresh_known": timestamp_valid,
        "health": "healthy" if fresh else ("stale" if timestamp_valid else "error"),
        "last_success_at": None,
        "last_triggered_at": triggered_at.isoformat(),
        "last_triggered_at_kst": triggered_at.astimezone(SEOUL_TIMEZONE).isoformat(),
        "timestamp_kind": "timer_trigger",
        "age_seconds": max(0, int((current - triggered_at).total_seconds())),
        "fresh_after_kst": cutoff_kst.isoformat(),
        "freshness_policy": "last trigger must be on or after yesterday 00:00 KST",
        "source": "node_systemd_timer_last_trigger_seconds",
    }]


def backup_summary_payload(backups):
    row = backups[0] if backups else None
    return {
        "items": backups,
        "available": bool(backups),
        "health": row.get("health", "unknown") if row else "unknown",
        "fresh": row.get("fresh") if row else None,
        "freshness_policy": (
            row.get("freshness_policy")
            if row
            else "last trigger must be on or after yesterday 00:00 KST"
        ),
    }


def safe_value(fn, fallback):
    try:
        return fn()
    except Exception as exc:
        return fallback, str(exc)


def collect_parallel(tasks):
    """Run independent collectors concurrently and return keyed results plus errors."""
    results = {}
    errors = []
    if not tasks:
        return results, errors
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = {
            executor.submit(fn): (name, fallback)
            for name, (fn, fallback) in tasks.items()
        }
        for future in as_completed(futures):
            name, fallback = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                results[name] = fallback
                errors.append(f"{name}: {exc}")
    return results, errors


def get_prometheus_alerts():
    try:
        alerts = prometheus_get("/api/v1/alerts").get("alerts", [])
    except Exception as exc:
        return [{
            "source": "prometheus",
            "state": "error",
            "name": "Prometheus alerts unavailable",
            "summary": str(exc),
            "labels": {"severity": "critical"},
            "active_at": None,
        }]
    rows = []
    for alert in alerts:
        labels = alert.get("labels", {})
        if labels_reference_excluded_node(labels):
            continue
        annotations = alert.get("annotations", {})
        rows.append(
            {
                "source": "prometheus",
                "state": alert.get("state", "unknown"),
                "name": labels.get("alertname", "-"),
                "summary": annotations.get("summary") or annotations.get("description") or "",
                "labels": labels,
                "active_at": alert.get("activeAt"),
            }
        )
    return rows


def build_legacy_problem_list(
    servers,
    targets,
    services,
    alerts,
    mooncen_failures,
    errors,
    backups=None,
):
    servers = [row for row in servers if not node_is_excluded(row.get("node"))]
    targets = [row for row in targets if not node_is_excluded(row.get("node"))]
    services = [row for row in services if not node_is_excluded(row.get("node"))]
    alerts = [
        row
        for row in alerts
        if not labels_reference_excluded_node(row.get("labels"))
    ]
    problems = []
    down_server_nodes = {
        row.get("node")
        for row in servers
        if row.get("up") != "UP" and row.get("node")
    }
    for row in servers:
        if row.get("up") != "UP":
            problems.append({
                "kind": "server",
                "severity": "critical",
                "key": f"server:{row.get('node', '-')}",
                "title": f"{row.get('node', '-')} 서버 응답 없음",
                "detail": f"role={row.get('role', '-')} uptime={row.get('uptime', '-')}",
            })
    for row in services:
        if not row.get("ok"):
            problems.append({
                "kind": "service",
                "severity": "critical",
                "key": f"service:{row.get('node', '-')}:{row.get('service', '-')}",
                "title": f"{row.get('node', '-')} / {row.get('service', '-')} 중지",
                "detail": "필수 서비스가 실행 중이 아닙니다.",
            })
    for row in targets:
        if row.get("health") != "up" and row.get("alerting") != "pending":
            if (
                row.get("node") in down_server_nodes
                and row.get("job") in ("node_exporter", "windows_exporter")
            ):
                continue
            problems.append({
                "kind": "target",
                "severity": "critical",
                "key": f"target:{row.get('node', '-')}:{row.get('job', '-')}",
                "title": f"{row.get('node', '-')} 메트릭 수집 실패",
                "detail": row.get("last_error") or row.get("instance") or "-",
            })
    for row in alerts:
        if row.get("state") != "firing":
            continue
        labels = row.get("labels") or {}
        name = row.get("name", "Prometheus alert")
        if name == "MoonCenExporterTargetDown" and labels.get("node") in down_server_nodes:
            continue
        service_alert = {
            "MoonCenGen1DbDown": ("gen1db", "db"),
            "MoonCenGen1WebApiDown": ("gen1web", "backend"),
            "MoonCenGen1WebNginxDown": ("gen1web", "frontend"),
            "MoonCenGen1CrawlerDown": ("gen1crawler", "crawler"),
        }.get(name)
        if service_alert and any(
            service.get("node") == service_alert[0]
            and service.get("service") == service_alert[1]
            and not service.get("ok")
            for service in services
        ):
            continue
        if name == "MoonCenCloudAppUnitDown" and any(
            service.get("node") == "cloud"
            and service.get("service") in {"frontend", "backend"}
            and not service.get("ok")
            for service in services
        ):
            continue
        identity_parts = [
            str(labels.get(key))
            for key in ("node", "instance", "unit", "mountpoint")
            if labels.get(key)
        ]
        identity = "|".join(identity_parts) or row.get("summary") or "-"
        problems.append({
            "kind": "alert",
            "severity": labels.get("severity", "warning"),
            "key": f"alert:{row.get('source', 'prometheus')}:{name}:{identity}",
            "title": name,
            "detail": row.get("summary") or "-",
        })
    if mooncen_failures:
        problems.append({
            "kind": "crawler",
            "severity": "warning",
            "key": "crawler:failures_24h",
            "title": f"최근 24시간 크롤러 실패 {mooncen_failures}건",
            "detail": "MoonCen 크롤러 실행 기록을 확인하세요.",
        })
    if backups is not None:
        if not backups:
            problems.append({
                "kind": "backup",
                "severity": "warning",
                "key": "backup:mooncen:unknown",
                "title": "MoonCen 백업 최신성 확인 불가",
                "detail": "cloud / mooncen-backup.timer 마지막 실행 지표가 없습니다.",
            })
        else:
            backup = backups[0]
            backup_health = str(backup.get("health") or "unknown").lower()
            if backup_health in ("error", "failed", "critical"):
                problems.append({
                    "kind": "backup",
                    "severity": "critical",
                    "key": "backup:mooncen:error",
                    "title": "MoonCen 백업 시각 오류",
                    "detail": (
                        f"last_triggered_at={backup.get('last_triggered_at') or '-'}"
                    ),
                })
            elif backup_health == "stale" or (
                backup.get("fresh_known") is True and not backup.get("fresh")
            ):
                problems.append({
                    "kind": "backup",
                    "severity": "warning",
                    "key": "backup:mooncen:stale",
                    "title": "MoonCen 백업 갱신 필요",
                    "detail": (
                        f"last_triggered_at={backup.get('last_triggered_at') or '-'}"
                    ),
                })
            elif backup_health not in ("healthy", "ok") and not backup.get("fresh"):
                problems.append({
                    "kind": "backup",
                    "severity": "warning",
                    "key": "backup:mooncen:unknown",
                    "title": "MoonCen 백업 최신성 확인 불가",
                    "detail": "백업 최신성을 판단할 수 있는 유효한 실행 시각이 없습니다.",
                })
    for index, error in enumerate(errors):
        problems.append({
            "kind": "adapter",
            "severity": "critical",
            "key": f"adapter:{index}",
            "title": "모니터링 데이터 수집 오류",
            "detail": str(error),
        })
    return problems


def build_problem_list(core_services, primary):
    """Build stable, logical problems only from the four user-facing services."""
    problems = []
    for row in core_services:
        service = str(row.get("service") or "unknown")
        if service not in CORE_SERVICE_ORDER:
            continue
        status = str(row.get("status") or "unknown")
        if status != "critical":
            continue
        label = row.get("label") or CORE_SERVICE_LABELS[service]
        problems.append({
            "kind": "service",
            "severity": "critical",
            "key": f"service:{service}",
            "title": f"{label} 실제 기능 장애",
            "detail": row.get("detail") or "유효한 기능 상태 증거가 없습니다.",
        })

    primary_status = str((primary or {}).get("status") or "unknown")
    if primary_status == "critical":
        expected_node = (primary or {}).get("expected_node") or "-"
        observed_node = (primary or {}).get("node") or "-"
        problems.append({
            "kind": "primary",
            "severity": "critical",
            "key": "primary:database",
            "title": "Primary DB 상태 이상",
            "detail": f"expected={expected_node} observed={observed_node}",
        })
    return problems


def operation_catalog():
    return [
        {"id": "restart_cloud_frontend", "label": "cloud frontend 재시작", "kind": "restart", "node": "cloud"},
        {"id": "restart_cloud_backend", "label": "cloud backend 재시작", "kind": "restart", "node": "cloud"},
        {"id": "restart_cloud_cloudflare", "label": "cloud cloudflare 재시작", "kind": "restart", "node": "cloud"},
        {"id": "restart_gen1db_postgresql", "label": "gen1db PostgreSQL 재시작", "kind": "restart", "node": "gen1db"},
        {"id": "restart_gen1web_backend", "label": "gen1web backend 재시작", "kind": "restart", "node": "gen1web"},
        {"id": "restart_gen1web_nginx", "label": "gen1web nginx 재시작", "kind": "restart", "node": "gen1web"},
        {"id": "restart_gen1crawler_crawler", "label": "gen1crawler crawler 재시작", "kind": "restart", "node": "gen1crawler"},
        {"id": "restart_wtr_linux_ollama", "label": "wtr-linux ollama 재시작", "kind": "restart", "node": "wtr-linux"},
        {"id": "wol_wtr_nas", "label": "wtr-nas WOL", "kind": "wol", "node": "wtr-nas"},
    ]


def operation_command(action_id):
    commands = {
        "restart_cloud_frontend": ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "ubuntu@cloud", "sudo -n systemctl restart mooncen-frontend.service"],
        "restart_cloud_backend": ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "ubuntu@cloud", "sudo -n systemctl restart mooncen-api.service"],
        "restart_cloud_cloudflare": ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "ubuntu@cloud", "sudo -n systemctl restart cloudflared.service"],
        "restart_gen1db_postgresql": ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "ubuntu@gen1db", "sudo -n systemctl restart postgresql.service"],
        "restart_gen1web_backend": ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "ubuntu@gen1web", "sudo -n systemctl restart mooncen-api.service"],
        "restart_gen1web_nginx": ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "ubuntu@gen1web", "sudo -n systemctl restart nginx.service"],
        "restart_gen1crawler_crawler": ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "sgm@gen1crawler", "sudo -n /usr/local/libexec/mooncen-ops-service crawler-once"],
        "restart_wtr_linux_ollama": ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "ubuntu@wtr-linux", "sudo -n systemctl restart ollama.service"],
        "wol_wtr_nas": ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "root@wtr-proxmox", "python3 - <<'PY'\nimport socket\nmac='00:11:32:90:1b:1c'.replace(':','')\npacket=bytes.fromhex('ff'*6 + mac*16)\ns=socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\ns.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)\ns.sendto(packet, ('192.168.0.255', 9))\nprint('sent')\nPY"],
    }
    return commands.get(action_id)


@app.get("/")
def index():
    return Response(
        "MoonCen Monitor API backend\n"
        "Use the Android app for the UI.\n"
        "Monitoring: /api/monitoring/core, /api/monitoring/crawler, "
        "/api/monitoring/summary, "
        "/api/monitoring/mooncen, "
        "/api/monitoring/servers, /api/monitoring/tailscale\n"
        "Operation: /api/operation/actions, /api/operation/run\n"
        "Prometheus: /metrics\n",
        mimetype="text/plain; charset=utf-8",
    )


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.get("/manifest.webmanifest")
def manifest():
    return jsonify(
        {
            "name": "MoonCen Monitor",
            "short_name": "MoonCen",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#101418",
            "theme_color": "#2f7d66",
            "icons": [],
        }
    )


@app.get("/api/core")
@app.get("/api/monitoring/core")
def core_summary():
    """Lightweight endpoint used by Android overview and background checks."""
    return jsonify(get_cached_core_snapshot())


@app.get("/api/summary")
@app.get("/api/monitoring/summary")
def summary():
    started = time.time()
    core = get_cached_core_snapshot()
    collected, errors = collect_parallel({
        "servers": (get_server_rows, []),
        "targets": (get_scrape_targets, []),
        "alerts": (get_prometheus_alerts, []),
        "backups": (backup_status, []),
    })
    servers = collected["servers"]
    targets = collected["targets"]
    alerts = collected["alerts"]
    backups = collected["backups"]
    down_servers = [row for row in servers if row.get("up") != "UP"]
    down_targets = [
        row
        for row in targets
        if row.get("health") != "up" and row.get("alerting") != "pending"
    ]
    active_alerts = [row for row in alerts if row.get("state") == "firing"]
    backup_stale = sum(
        1
        for row in backups
        if row.get("health") == "stale"
        or (row.get("fresh_known") is True and not row.get("fresh"))
    )
    backup_error = sum(
        1
        for row in backups
        if row.get("health") in ("error", "failed", "critical")
    )
    backup_unknown = int(not backups) + sum(
        1
        for row in backups
        if row.get("health")
        not in ("healthy", "ok", "stale", "error", "failed", "critical")
        and not row.get("fresh")
    )
    counts = dict(core["counts"])
    counts.update({
        "servers": len(servers),
        "down_servers": len(down_servers),
        "down_targets": len(down_targets),
        "active_alerts": len(active_alerts),
        "mooncen_failures": 0,
        "backup_stale": backup_stale,
        "backup_error": backup_error,
        "backup_unknown": backup_unknown,
    })
    return jsonify({
        **core,
        "latency_ms": round((time.time() - started) * 1000),
        "errors": errors,
        "counts": counts,
        "servers": servers,
        "targets": targets,
        "alerts": alerts,
        "backup": backup_summary_payload(backups),
    })


@app.get("/api/mooncen")
@app.get("/api/monitoring/mooncen")
def mooncen_summary():
    core = get_cached_core_snapshot()
    collected, errors = collect_parallel({"backups": (backup_status, [])})
    backups = collected["backups"]
    by_service = {row["service"]: row for row in core["core_services"]}
    crawler = by_service["crawler"]
    database = by_service["database"]
    return jsonify({
        **core,
        "errors": errors,
        "crawler": {
            "available": crawler.get("functional_ok") is not None,
            "ok": crawler.get("ok", False),
            "status": crawler.get("status", "unknown"),
            "detail": crawler.get("detail", ""),
            "success_24h": 0,
            "failed_24h": 1 if crawler.get("status") == "critical" else 0,
            "collected_24h": 0,
            "latest_failures": [],
            "summary_24h": [],
        },
        "backup": backup_summary_payload(backups),
        "ops": {
            "ok": database.get("functional_ok") is True,
            "database": database.get("status", "unknown"),
            "tables": {},
            "latest_crawler_run": None,
            "latest_quality_check": None,
        },
    })


@app.get("/api/crawler")
@app.get("/api/monitoring/crawler")
def crawler_monitoring_summary():
    """Bounded read-only crawler operations snapshot for Android."""
    return jsonify(collect_crawler_monitoring_snapshot())


@app.get("/api/servers")
@app.get("/api/monitoring/servers")
def servers_summary():
    result = safe_value(get_server_rows, [])
    if isinstance(result, tuple):
        servers, error = result
        errors = [error]
    else:
        servers = result
        errors = []
    return jsonify({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "errors": errors,
        "servers": servers,
        "tailscale": get_tailscale_status(),
    })


@app.get("/api/tailscale")
@app.get("/api/monitoring/tailscale")
def tailscale_summary():
    return jsonify(get_tailscale_status())


@app.get("/api/operations")
@app.get("/api/operation/actions")
def operations():
    if not OPERATION_ENABLED:
        return jsonify({"error": "not found"}), 404
    if not OPERATION_TOKEN:
        return jsonify({"error": "operation token is not configured"}), 503
    return jsonify({
        "operation_enabled": True,
        "operation_token_required": True,
        "operation_token_configured": True,
        "actions": operation_catalog(),
    })


@app.post("/api/operations/run")
@app.post("/api/operation/run")
def run_operation():
    if not OPERATION_ENABLED:
        return jsonify({"error": "not found"}), 404
    if not OPERATION_TOKEN:
        return jsonify({"ok": False, "error": "operation token is not configured"}), 503
    if not operation_token_valid():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    action_id = str(payload.get("action") or "").strip()
    command = operation_command(action_id)
    if not command:
        return jsonify({"ok": False, "error": "unknown action"}), 400
    started = time.time()
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS)
        return jsonify({
            "ok": result.returncode == 0,
            "action": action_id,
            "returncode": result.returncode,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-2000:],
            "elapsed_seconds": round(time.time() - started, 2),
        })
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "action": action_id, "error": "timeout"}), 504
    except Exception as exc:
        return jsonify({"ok": False, "action": action_id, "error": str(exc)}), 500


@app.get("/api/trends")
@app.get("/api/monitoring/trends")
def trends():
    now = int(time.time())
    try:
        hours = int(request.args.get("hours", "6"))
    except (TypeError, ValueError):
        return jsonify({"error": "hours must be an integer between 1 and 168"}), 400
    if hours < 1 or hours > 168:
        return jsonify({"error": "hours must be an integer between 1 and 168"}), 400
    start = now - hours * 3600
    step = max(60, int((now - start) / 120))
    queries = {
        "cpu": '100 - (avg by (node) (rate(node_cpu_seconds_total{mode="idle",node!=""}[5m])) * 100) or 100 - (avg by (node) (rate(windows_cpu_time_total{mode="idle",node!=""}[5m])) * 100)',
        "mem": '(1 - (node_memory_MemAvailable_bytes{node!=""} / node_memory_MemTotal_bytes{node!=""})) * 100 or (1 - ((node_memory_free_bytes{node!=""} + node_memory_inactive_bytes{node!=""}) / node_memory_total_bytes{node!=""})) * 100 or (1 - (windows_memory_available_bytes{node!=""} / windows_memory_physical_total_bytes{node!=""})) * 100',
        "temp": temperature_promql('node!=""'),
    }
    result = {}
    errors = []
    for name, query in queries.items():
        try:
            result[name] = query_range(query, start, now, step)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            result[name] = []
    return jsonify({"start": start, "end": now, "step": step, "series": result, "errors": errors})


@app.get("/metrics")
def metrics():
    lines = build_ops_metrics()
    return Response("\n".join(lines) + "\n", mimetype="text/plain; version=0.0.4; charset=utf-8")


def add_metric(lines, name, labels, value):
    label_text = ",".join(f'{key}="{prometheus_label(val)}"' for key, val in labels.items())
    lines.append(f"{name}{{{label_text}}} {prometheus_float(value)}")


def query_values_by_node(query):
    values = {}
    for item in query_vector(query):
        node = item.get("metric", {}).get("node")
        if node:
            values[node] = item.get("value", [None, None])[1]
    return values


def build_scrape_target_metrics():
    lines = []
    try:
        data = prometheus_get("/api/v1/targets", {"state": "any"}, timeout=4)
        targets = data.get("activeTargets", [])
    except Exception as exc:
        add_metric(
            lines,
            "mooncen_scrape_target_up",
            {
                "node": "prometheus",
                "job": "targets",
                "instance": PROMETHEUS_URL,
                "role": "monitoring",
                "alerting": "enabled",
                "health": "down",
                "last_error": str(exc)[:180],
            },
            0,
        )
        return lines

    for target in targets:
        labels = target.get("labels", {})
        discovered = target.get("discoveredLabels", {})
        health = target.get("health") or "unknown"
        add_metric(
            lines,
            "mooncen_scrape_target_up",
            {
                "node": labels.get("node") or discovered.get("node") or labels.get("instance") or "",
                "job": labels.get("job") or discovered.get("job") or "",
                "instance": labels.get("instance") or discovered.get("__address__") or "",
                "role": labels.get("role") or discovered.get("role") or "",
                "alerting": labels.get("alerting") or discovered.get("alerting") or "",
                "health": health,
                "last_error": target.get("lastError") or "",
            },
            1 if health == "up" else 0,
        )
    return lines


def build_node_summary_metrics():
    lines = []
    for item in get_server_rows():
        add_metric(
            lines,
            "mooncen_node_summary_info",
            {
                "node": item.get("node", ""),
                "up": item.get("up", "DOWN"),
                "cpu": item.get("cpu", "-"),
                "mem": item.get("mem", "-"),
                "disk": item.get("disk", "-"),
                "temp": item.get("temp", "-"),
                "uptime": item.get("uptime", "-"),
                "role": item.get("role", ""),
                "alerting": item.get("alerting", ""),
            },
            1,
        )
    return lines


def build_service_metrics():
    lines = []
    for row in get_service_checks():
        status = str(row.get("status") or "unknown")
        if status not in CORE_STATUS_VALUES:
            status = "unknown"
        labels = {
            "node": row.get("primary_node") or row.get("node", ""),
            "service": row.get("service", ""),
        }
        for candidate_status in CORE_STATUS_VALUES:
            add_metric(
                lines,
                "mooncen_core_service_status",
                {**labels, "status": candidate_status},
                1 if candidate_status == status else 0,
            )
        if row.get("runtime_ok") is not None:
            add_metric(
                lines,
                "mooncen_core_service_runtime_up",
                labels,
                1 if row.get("runtime_ok") else 0,
            )
        if row.get("functional_ok") is not None:
            add_metric(
                lines,
                "mooncen_core_service_functional_up",
                labels,
                1 if row.get("functional_ok") else 0,
            )
        if status not in ("healthy", "critical"):
            continue
        add_metric(
            lines,
            "mooncen_service_up",
            labels,
            1 if status == "healthy" else 0,
        )
    return lines


def build_legacy_ops_metrics():
    lines = [
        "# HELP mooncen_scrape_target_up Prometheus scrape target health with last error label",
        "# TYPE mooncen_scrape_target_up gauge",
        "# HELP mooncen_node_summary_info Current server summary for monitoring clients",
        "# TYPE mooncen_node_summary_info gauge",
        "# HELP mooncen_service_up Required MoonCen service status",
        "# TYPE mooncen_service_up gauge",
        "# HELP mooncen_ops_api_up MoonCen ops API availability",
        "# TYPE mooncen_ops_api_up gauge",
        "# HELP mooncen_ops_table_available MoonCen ops backing table availability",
        "# TYPE mooncen_ops_table_available gauge",
        "# HELP mooncen_ops_latest_crawler_run_timestamp_seconds Latest crawler run timestamp",
        "# TYPE mooncen_ops_latest_crawler_run_timestamp_seconds gauge",
        "# HELP mooncen_ops_latest_quality_check_timestamp_seconds Latest course quality check timestamp",
        "# TYPE mooncen_ops_latest_quality_check_timestamp_seconds gauge",
        "# HELP mooncen_ops_crawler_runs_24h Crawler runs in the last 24 hours",
        "# TYPE mooncen_ops_crawler_runs_24h gauge",
        "# HELP mooncen_ops_crawler_collected_24h Crawler collected rows in the last 24 hours",
        "# TYPE mooncen_ops_crawler_collected_24h gauge",
        "# HELP mooncen_ops_crawler_inserted_24h Crawler inserted rows in the last 24 hours",
        "# TYPE mooncen_ops_crawler_inserted_24h gauge",
        "# HELP mooncen_ops_crawler_updated_24h Crawler updated rows in the last 24 hours",
        "# TYPE mooncen_ops_crawler_updated_24h gauge",
        "# HELP mooncen_ops_crawler_skipped_24h Crawler skipped rows in the last 24 hours",
        "# TYPE mooncen_ops_crawler_skipped_24h gauge",
        "# HELP mooncen_ops_crawler_avg_duration_seconds Crawler average run duration seconds in the last 24 hours",
        "# TYPE mooncen_ops_crawler_avg_duration_seconds gauge",
        "# HELP mooncen_ops_crawler_latest_started_timestamp_seconds Latest crawler start timestamp by source and status",
        "# TYPE mooncen_ops_crawler_latest_started_timestamp_seconds gauge",
        "# HELP mooncen_ops_crawler_latest_failure_info Latest crawler failure rows as labels",
        "# TYPE mooncen_ops_crawler_latest_failure_info gauge",
        "# HELP mooncen_ops_quality_available Course quality summary availability",
        "# TYPE mooncen_ops_quality_available gauge",
        "# HELP mooncen_ops_quality_grade_count Course quality count by grade",
        "# TYPE mooncen_ops_quality_grade_count gauge",
        "# HELP mooncen_ops_quality_provider_count Course quality count by provider and grade",
        "# TYPE mooncen_ops_quality_provider_count gauge",
        "# HELP mooncen_ops_quality_missing_field_count Course quality missing field count",
        "# TYPE mooncen_ops_quality_missing_field_count gauge",
    ]

    lines.extend(build_scrape_target_metrics())
    lines.extend(build_node_summary_metrics())
    lines.extend(build_service_metrics())

    try:
        health = ops_get("/health")
        lines.append("mooncen_ops_api_up 1")
        for table, available in (health.get("tables") or {}).items():
            add_metric(lines, "mooncen_ops_table_available", {"table": table}, 1 if available else 0)
        lines.append(
            "mooncen_ops_latest_crawler_run_timestamp_seconds "
            f"{timestamp_seconds(health.get('latest_crawler_run'))}"
        )
        lines.append(
            "mooncen_ops_latest_quality_check_timestamp_seconds "
            f"{timestamp_seconds(health.get('latest_quality_check'))}"
        )
    except Exception as exc:
        lines.append("mooncen_ops_api_up 0")
        add_metric(lines, "mooncen_ops_crawler_latest_failure_info", {"target_key": "ops_api", "source_type": "api", "error_type": type(exc).__name__, "error_message": str(exc)[:180]}, 1)
        return lines

    try:
        crawler = ops_get("/crawler-summary")
        for row in crawler.get("summary_24h") or []:
            labels = {"source_type": row.get("source_type", "unknown"), "status": row.get("status", "unknown")}
            add_metric(lines, "mooncen_ops_crawler_runs_24h", labels, row.get("run_count"))
            add_metric(lines, "mooncen_ops_crawler_collected_24h", labels, row.get("collected_count"))
            add_metric(lines, "mooncen_ops_crawler_inserted_24h", labels, row.get("inserted_count"))
            add_metric(lines, "mooncen_ops_crawler_updated_24h", labels, row.get("updated_count"))
            add_metric(lines, "mooncen_ops_crawler_skipped_24h", labels, row.get("skipped_count"))
            add_metric(lines, "mooncen_ops_crawler_avg_duration_seconds", labels, row.get("avg_duration_seconds"))
            add_metric(lines, "mooncen_ops_crawler_latest_started_timestamp_seconds", labels, timestamp_seconds(row.get("latest_started_at")))
        for row in crawler.get("latest_failures") or []:
            labels = {
                "target_key": row.get("target_key", ""),
                "source_type": row.get("source_type", ""),
                "crawler_name": str(row.get("crawler_name", ""))[-120:],
                "error_type": row.get("error_type", ""),
                "error_message": str(row.get("error_message", ""))[:180],
            }
            add_metric(lines, "mooncen_ops_crawler_latest_failure_info", labels, 1)
    except Exception as exc:
        add_metric(lines, "mooncen_ops_crawler_latest_failure_info", {"target_key": "crawler_summary", "source_type": "api", "error_type": type(exc).__name__, "error_message": str(exc)[:180]}, 1)

    try:
        quality = ops_get("/course-quality-summary")
        available = bool(quality.get("available"))
        lines.append(f"mooncen_ops_quality_available {1 if available else 0}")
        for row in quality.get("grades") or []:
            add_metric(lines, "mooncen_ops_quality_grade_count", {"grade": row.get("grade", "unknown")}, row.get("course_count") or row.get("count"))
        for row in quality.get("providers") or []:
            labels = {"provider": row.get("source_type") or row.get("provider") or "unknown", "grade": row.get("grade", "unknown")}
            add_metric(lines, "mooncen_ops_quality_provider_count", labels, row.get("course_count") or row.get("count"))
        for row in quality.get("missing_fields") or []:
            labels = {"field": row.get("field") or row.get("missing_field") or "unknown"}
            add_metric(lines, "mooncen_ops_quality_missing_field_count", labels, row.get("course_count") or row.get("count"))
    except Exception:
        lines.append("mooncen_ops_quality_available 0")

    return lines


def build_ops_metrics():
    lines = [
        "# HELP mooncen_scrape_target_up Prometheus scrape target health with last error label",
        "# TYPE mooncen_scrape_target_up gauge",
        "# HELP mooncen_node_summary_info Current server summary for monitoring clients",
        "# TYPE mooncen_node_summary_info gauge",
        "# HELP mooncen_service_up Legacy definitive core service status",
        "# TYPE mooncen_service_up gauge",
        "# HELP mooncen_core_service_status Current core service status",
        "# TYPE mooncen_core_service_status gauge",
        "# HELP mooncen_core_service_runtime_up Nullable core service runtime evidence",
        "# TYPE mooncen_core_service_runtime_up gauge",
        "# HELP mooncen_core_service_functional_up Nullable core service functional evidence",
        "# TYPE mooncen_core_service_functional_up gauge",
        "# HELP mooncen_primary_status Current observed primary status",
        "# TYPE mooncen_primary_status gauge",
    ]
    lines.extend(build_scrape_target_metrics())
    lines.extend(build_node_summary_metrics())
    lines.extend(build_service_metrics())
    primary = get_cached_core_snapshot()["primary"]
    primary_status = str(primary.get("status") or "unknown")
    if primary_status not in CORE_STATUS_VALUES:
        primary_status = "unknown"
    primary_labels = {
        "node": primary.get("node") or "unknown",
        "expected_node": primary.get("expected_node") or "unknown",
    }
    for candidate_status in CORE_STATUS_VALUES:
        add_metric(
            lines,
            "mooncen_primary_status",
            {**primary_labels, "status": candidate_status},
            1 if candidate_status == primary_status else 0,
        )
    return lines


INDEX_HTML = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="manifest" href="/manifest.webmanifest">
  <title>MoonCen Monitor</title>
  <script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
  <style>
    :root {
      color-scheme: dark;
      --bg: #101418;
      --panel: #171d22;
      --panel-2: #1d242a;
      --line: #303a42;
      --text: #edf3f0;
      --muted: #9facaa;
      --green: #58b88f;
      --red: #ef6262;
      --yellow: #e2b84d;
      --blue: #6ea8fe;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg);
      color: var(--text);
      letter-spacing: 0;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 2;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 18px;
      border-bottom: 1px solid var(--line);
      background: rgba(16, 20, 24, 0.94);
      backdrop-filter: blur(10px);
    }
    h1 { margin: 0; font-size: 18px; font-weight: 700; }
    h2 { margin: 0 0 10px; font-size: 14px; color: #dce8e3; }
    button, a.button {
      width: 36px;
      height: 36px;
      display: inline-grid;
      place-items: center;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel-2);
      color: var(--text);
      cursor: pointer;
      text-decoration: none;
    }
    button:hover, a.button:hover { border-color: var(--green); }
    main { padding: 14px; max-width: 1440px; margin: 0 auto; }
    .toolbar { display: flex; gap: 8px; align-items: center; }
    .status-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--muted); }
    .status-dot.ok { background: var(--green); }
    .status-dot.warn { background: var(--yellow); }
    .status-dot.bad { background: var(--red); }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      min-width: 0;
    }
    .metric { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
    .metric strong { font-size: 26px; }
    .metric span { color: var(--muted); font-size: 13px; }
    .wide { grid-column: span 2; }
    .full { grid-column: 1 / -1; }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; }
    th, td {
      padding: 8px 7px;
      border-bottom: 1px solid #263038;
      text-align: left;
      font-size: 13px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    th { color: var(--muted); font-weight: 600; }
    tr:last-child td { border-bottom: 0; }
    .pill {
      display: inline-flex;
      align-items: center;
      height: 22px;
      padding: 0 8px;
      border-radius: 999px;
      background: #293138;
      color: var(--text);
      font-size: 12px;
      max-width: 100%;
    }
    .pill.ok { background: rgba(88, 184, 143, .18); color: #8ee4bd; }
    .pill.bad { background: rgba(239, 98, 98, .18); color: #ff9b9b; }
    .pill.warn { background: rgba(226, 184, 77, .16); color: #f3d47a; }
    .service-list {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
    }
    .service {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #141a1f;
      min-width: 0;
    }
    .service b, .service span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    canvas { width: 100%; height: 220px; display: block; }
    .muted { color: var(--muted); }
    .errors { color: #ffb0b0; font-size: 12px; margin-top: 8px; white-space: pre-wrap; }
    @media (max-width: 980px) {
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .service-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 640px) {
      header { padding: 10px 12px; }
      main { padding: 10px; }
      .grid { grid-template-columns: 1fr; }
      .wide { grid-column: auto; }
      .service-list { grid-template-columns: 1fr; }
      th, td { font-size: 12px; padding: 7px 5px; }
      .hide-sm { display: none; }
    }
  </style>
</head>
<body>
  <header>
    <div style="display:flex;align-items:center;gap:10px">
      <span id="statusDot" class="status-dot"></span>
      <h1>MoonCen Monitor</h1>
      <span id="updated" class="muted"></span>
    </div>
    <div class="toolbar">
      <button id="notifyBtn" title="브라우저 알림"><i data-lucide="bell"></i></button>
      <button id="refreshBtn" title="새로고침"><i data-lucide="refresh-cw"></i></button>
    </div>
  </header>
  <main>
    <section class="grid">
      <div class="panel"><h2>Servers</h2><div class="metric"><strong id="serverCount">-</strong><span id="serverDown">-</span></div></div>
      <div class="panel"><h2>Services</h2><div class="metric"><strong id="serviceCount">-</strong><span id="serviceFail">-</span></div></div>
      <div class="panel"><h2>Scrape Targets</h2><div class="metric"><strong id="targetCount">-</strong><span id="targetDown">-</span></div></div>
      <div class="panel"><h2>Alerts</h2><div class="metric"><strong id="alertCount">-</strong><span id="latency">-</span></div></div>

      <div class="panel wide"><h2>CPU Trend</h2><canvas id="cpuChart"></canvas></div>
      <div class="panel wide"><h2>Memory Trend</h2><canvas id="memChart"></canvas></div>
      <div class="panel wide"><h2>Temperature Trend</h2><canvas id="tempChart"></canvas></div>
      <div class="panel wide"><h2>Services</h2><div id="services" class="service-list"></div></div>

      <div class="panel full"><h2>Server Summary</h2><div style="overflow:auto"><table><thead><tr><th>node</th><th>up</th><th>cpu</th><th>mem</th><th>disk</th><th>temp</th><th class="hide-sm">uptime</th><th>role</th></tr></thead><tbody id="servers"></tbody></table></div></div>
      <div class="panel full"><h2>Alerts</h2><div style="overflow:auto"><table><thead><tr><th>source</th><th>state</th><th>name</th><th>summary</th></tr></thead><tbody id="alerts"></tbody></table></div></div>
      <div class="panel full"><h2>Scrape Targets</h2><div style="overflow:auto"><table><thead><tr><th>node</th><th>job</th><th>health</th><th>alerting</th><th>last error</th></tr></thead><tbody id="targets"></tbody></table></div><div id="errors" class="errors"></div></div>
    </section>
  </main>
  <script>
    const state = { lastProblems: new Set(), token: localStorage.getItem("monitorToken") || "" };
    const q = (id) => document.getElementById(id);
    const authOptions = () => state.token
      ? { headers: { "X-App-Token": state.token } }
      : {};
    const pill = (text, cls = "") => `<span class="pill ${cls}">${escapeHtml(text)}</span>`;
    const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

    function notify(title, body) {
      if (!("Notification" in window) || Notification.permission !== "granted") return;
      new Notification(title, { body, tag: title });
    }

    function problemKey(data) {
      return new Set((data.problems || []).map(x => x.key));
    }

    function handleNotifications(data) {
      const next = problemKey(data);
      const added = [...next].filter(x => !state.lastProblems.has(x));
      if (added.length) notify("MoonCen 경고", added.slice(0, 5).join("\n"));
      state.lastProblems = next;
    }

    function renderSummary(data) {
      q("serverCount").textContent = data.counts.servers;
      q("serverDown").textContent = `${data.counts.down_servers} down`;
      q("serviceCount").textContent = data.services.length;
      q("serviceFail").textContent = `${data.counts.failing_services} failing`;
      q("targetCount").textContent = data.targets.length;
      q("targetDown").textContent = `${data.counts.down_targets} down`;
      q("alertCount").textContent = data.counts.active_alerts;
      q("latency").textContent = `${data.latency_ms}ms`;
      q("updated").textContent = new Date(data.generated_at).toLocaleTimeString();
      const statusClass = data.status === "critical" ? "bad" : (data.status === "healthy" ? "ok" : "warn");
      q("statusDot").className = `status-dot ${statusClass}`;
      q("errors").textContent = data.errors.join("\n");
    }

    function renderServices(rows) {
      const labels = { healthy: "OK", warning: "WARN", critical: "DOWN", unknown: "UNKNOWN" };
      q("services").innerHTML = rows.map(x => {
        const status = x.status || (x.ok ? "healthy" : "critical");
        const cls = status === "healthy" ? "ok" : (status === "critical" ? "bad" : "warn");
        return `<div class="service"><b>${escapeHtml(x.node)} / ${escapeHtml(x.service)}</b>${pill(labels[status] || "UNKNOWN", cls)}</div>`;
      }).join("");
    }

    function renderServers(rows) {
      q("servers").innerHTML = rows.map(x => `<tr>
        <td>${escapeHtml(x.node)}</td><td>${pill(x.up, x.up === "UP" ? "ok" : "bad")}</td>
        <td>${escapeHtml(x.cpu)}</td><td>${escapeHtml(x.mem)}</td><td>${escapeHtml(x.disk)}</td>
        <td>${escapeHtml(x.temp)}</td><td class="hide-sm">${escapeHtml(x.uptime)}</td><td>${escapeHtml(x.role)}</td>
      </tr>`).join("");
    }

    function renderAlerts(rows) {
      q("alerts").innerHTML = rows.length ? rows.map(x => `<tr>
        <td>${escapeHtml(x.source || "-")}</td><td>${pill(x.state || "-", x.state === "error" ? "bad" : "warn")}</td>
        <td>${escapeHtml(x.name)}</td><td>${escapeHtml(x.summary)}</td>
      </tr>`).join("") : `<tr><td colspan="4" class="muted">활성 알림 없음</td></tr>`;
    }

    function renderTargets(rows) {
      q("targets").innerHTML = rows.map(x => `<tr>
        <td>${escapeHtml(x.node)}</td><td>${escapeHtml(x.job)}</td>
        <td>${pill(x.health, x.health === "up" ? "ok" : (x.alerting === "pending" ? "warn" : "bad"))}</td>
        <td>${escapeHtml(x.alerting)}</td><td>${escapeHtml(x.last_error || "-")}</td>
      </tr>`).join("");
    }

    function drawChart(id, series, unit) {
      const canvas = q(id);
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(320, Math.floor(rect.width * dpr));
      canvas.height = Math.floor(220 * dpr);
      const ctx = canvas.getContext("2d");
      const w = canvas.width, h = canvas.height;
      ctx.clearRect(0, 0, w, h);
      ctx.strokeStyle = "#303a42";
      ctx.lineWidth = 1 * dpr;
      for (let i = 1; i < 4; i++) {
        const y = (h * i) / 4;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
      }
      const colors = ["#58b88f", "#6ea8fe", "#e2b84d", "#ef6262", "#c792ea", "#89ddff", "#ffcb6b"];
      const allValues = series.flatMap(s => s.values.map(v => Number(v[1])).filter(Number.isFinite));
      const max = Math.max(unit === "C" ? 80 : 100, ...allValues);
      series.forEach((s, idx) => {
        const values = s.values || [];
        if (values.length < 2) return;
        ctx.strokeStyle = colors[idx % colors.length];
        ctx.lineWidth = 2 * dpr;
        ctx.beginPath();
        values.forEach((v, i) => {
          const x = (i / (values.length - 1)) * w;
          const y = h - (Math.max(0, Number(v[1])) / max) * (h - 8 * dpr) - 4 * dpr;
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.stroke();
      });
    }

    async function refresh() {
      const resp = await fetch("/api/summary", authOptions());
      if (resp.status === 401) {
        const token = prompt("앱 토큰을 입력하세요");
        if (token) { localStorage.setItem("monitorToken", token); state.token = token; return refresh(); }
        return;
      }
      const data = await resp.json();
      renderSummary(data);
      renderServices(data.services);
      renderServers(data.servers);
      renderAlerts(data.alerts.filter(x => !["inactive", "normal"].includes(x.state)));
      renderTargets(data.targets);
      handleNotifications(data);
    }

    async function refreshTrends() {
      const resp = await fetch("/api/trends", authOptions());
      if (!resp.ok) return;
      const data = await resp.json();
      drawChart("cpuChart", data.series.cpu || [], "%");
      drawChart("memChart", data.series.mem || [], "%");
      drawChart("tempChart", data.series.temp || [], "C");
    }

    q("refreshBtn").addEventListener("click", () => { refresh(); refreshTrends(); });
    q("notifyBtn").addEventListener("click", async () => {
      if (!("Notification" in window)) return alert("이 브라우저는 알림을 지원하지 않습니다.");
      const permission = await Notification.requestPermission();
      if (permission === "granted") notify("MoonCen Monitor", "브라우저 알림이 켜졌습니다.");
    });

    if (window.lucide) lucide.createIcons();
    refresh();
    refreshTrends();
    setInterval(refresh, 15000);
    setInterval(refreshTrends, 60000);
    addEventListener("resize", () => refreshTrends());
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host=APP_HOST, port=APP_PORT)
