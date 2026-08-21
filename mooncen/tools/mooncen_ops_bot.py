#!/usr/bin/env python3
"""Small Telegram monitoring bot for MoonCen operations.

The bot reports abnormal states and exposes read-only operation menus. It does
not promote PostgreSQL, move Cloudflare tunnels, or enable automatic failover.
Failover remains a manual operator procedure.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
import requests


APP_DIR = Path(os.getenv("APP_DIR", "/opt/mooncen"))
STATE_DIR = Path(os.getenv("MOONCEN_BOT_STATE_DIR", APP_DIR / "failover"))
FAILOVER_LOG = Path(os.getenv("FAILOVER_LOG", STATE_DIR / "failover.log"))
FAIL_COUNT_FILE = Path(os.getenv("FAIL_COUNT_FILE", STATE_DIR / "cloud_fail_count"))
ENABLE_FILE = Path(os.getenv("ENABLE_FILE", STATE_DIR / "enable_auto_failover"))
BOT_STATE_FILE = Path(os.getenv("MOONCEN_BOT_STATE_FILE", STATE_DIR / "bot_state.json"))
GATE_DISABLE_FILE = Path(os.getenv("CLOUDFLARE_GATE_DISABLE_FILE", STATE_DIR / "disable_cloudflare_gate"))


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv(APP_DIR / ".env")
load_dotenv(STATE_DIR / "failover.env")

TOKEN = os.getenv("MOONCEN_BOT_TOKEN", "").strip()
CHAT_IDS = {
    item.strip()
    for item in os.getenv("MOONCEN_BOT_CHAT_ID", os.getenv("MOONCEN_BOT_CHAT_IDS", "")).split(",")
    if item.strip()
}
POLL_TIMEOUT = int(os.getenv("MOONCEN_BOT_POLL_TIMEOUT", "25"))
MONITOR_INTERVAL = int(os.getenv("MOONCEN_BOT_MONITOR_INTERVAL", "10"))
FAILOVER_LOG_ALERTS = os.getenv("MOONCEN_BOT_FAILOVER_LOG_ALERTS", "0").strip().lower() in {"1", "true", "yes", "on"}
STARTUP_MESSAGE = os.getenv("MOONCEN_BOT_STARTUP_MESSAGE", "0").strip().lower() in {"1", "true", "yes", "on"}
MENU_ALIASES = {"menu", "\uba54\ub274", "operation", "operations", "\uc6b4\uc601"}
TEXT_COMMAND_ALIASES = {
    "summary": "/summary",
    "\uc694\uc57d": "/summary",
    "status": "/status",
    "\uc0c1\ud0dc": "/status",
    "monitor": "/monitoring",
    "monitoring": "/monitoring",
    "\ubaa8\ub2c8\ud130\ub9c1": "/monitoring",
    "services": "/services",
    "service": "/services",
    "\uc11c\ube44\uc2a4": "/services",
    "public": "/public_status",
    "public_status": "/public_status",
    "replica": "/replica_status",
    "replica_status": "/replica_status",
    "cloudflare": "/cloudflare_status",
    "cloudflare_status": "/cloudflare_status",
    "crawler": "/crawler_status",
    "crawler_status": "/crawler_status",
    "staging": "/staging_status",
    "staging_status": "/staging_status",
    "ai": "/ai_status",
    "ai_status": "/ai_status",
    "backup": "/backup_status",
    "backup_status": "/backup_status",
    "failover": "/failover_status",
    "failover_status": "/failover_status",
    "failover_log": "/failover_log",
    "failover_logs": "/failover_log",
    "log": "/failover_log",
    "logs": "/failover_log",
    "\ub85c\uadf8": "/failover_log",
}
MENU_KEYBOARD = [
    ["/summary", "/monitoring"],
    ["/services"],
    ["/replica_status", "/cloudflare_status"],
    ["/crawler_status", "/staging_status"],
    ["/ai_status", "/backup_status"],
    ["/failover_status", "/failover_log"],
    ["/manual_failover"],
    ["MENU"],
]


def run(command: str, timeout: int = 20) -> str:
    """Run one of this module's fixed diagnostic pipelines through Bash."""
    proc = subprocess.run(
        ["/bin/bash", "-o", "pipefail", "-c", command],
        shell=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    output = (proc.stdout or "").strip()
    if proc.returncode != 0:
        return f"$ {command}\nexit={proc.returncode}\n{output}".strip()
    return output


def telegram(method: str, payload: dict, timeout: int = 30) -> dict:
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    try:
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        status_text = f" status={status}" if status is not None else ""
        raise RuntimeError(
            f"Telegram API request failed method={method} type={type(exc).__name__}{status_text}"
        ) from None


def operations_keyboard() -> dict:
    return {
        "keyboard": MENU_KEYBOARD,
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "is_persistent": True,
        "input_field_placeholder": "MENU / \uba54\ub274",
    }


def should_show_menu_keyboard(text: str) -> bool:
    command = text.strip()
    normalized = command.lower()
    if normalized in MENU_ALIASES:
        return True
    base = command.split(maxsplit=1)[0].split("@", 1)[0].lower()
    return base in {"/start", "/help", "/menu", "/operations", "/operation"}


def is_command_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    normalized = stripped.lower()
    return stripped.startswith("/") or normalized in MENU_ALIASES or normalized in TEXT_COMMAND_ALIASES


def send_message(chat_id: str, text: str, reply_markup: dict | None = None) -> None:
    max_len = 3900
    parts = [text[i : i + max_len] for i in range(0, len(text), max_len)] or [""]
    for index, part in enumerate(parts):
        payload = {
            "chat_id": chat_id,
            "text": part,
            "disable_web_page_preview": True,
        }
        if reply_markup and index == 0:
            payload["reply_markup"] = reply_markup
        telegram("sendMessage", payload)


def broadcast(text: str) -> None:
    for chat_id in sorted(CHAT_IDS):
        try:
            send_message(chat_id, text)
        except Exception as exc:  # noqa: BLE001
            print(f"failed to send Telegram message to {chat_id}: {exc}", file=sys.stderr)


def is_authorized(chat_id: str) -> bool:
    return bool(CHAT_IDS) and chat_id in CHAT_IDS


def service_state(*names: str) -> str:
    quoted = " ".join(shlex.quote(name) for name in names)
    return run(f"systemctl is-active {quoted} 2>/dev/null | paste -sd ' ' - || true")


def service_enabled(*names: str) -> str:
    quoted = " ".join(shlex.quote(name) for name in names)
    return run(f"systemctl is-enabled {quoted} 2>/dev/null | paste -sd ' ' - || true")


def crawler_db_override() -> str:
    return run(
        "systemctl show mooncen-crawler -p Environment --value "
        "| tr ' ' '\\n' | grep -E '^DB_HOST=|^DB_PORT=' | paste -sd ' ' - || true"
    )


def crawler_env_summary() -> str:
    return run(
        "systemctl show mooncen-crawler -p Environment --value "
        "| tr ' ' '\\n' "
        "| grep -E '^DB_HOST=|^DB_PORT=|^CRAWL_WRITE_MODE=|^CRAWL_STAGING_DB_HOST=|^CRAWL_STAGING_DB_PORT=|^PRIMARY_DB_HOST=' "
        "| paste -sd ' ' - || true"
    )


def db_role() -> str:
    return run("sudo -u postgres psql -Atqc \"SELECT CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END;\"")


def fail_count() -> str:
    if not FAIL_COUNT_FILE.exists():
        return "0"
    value = FAIL_COUNT_FILE.read_text(encoding="utf-8", errors="ignore").strip()
    return value if value.isdigit() else "0"


def http_status(url: str, timeout: int = 8) -> str:
    return run(f"curl -fsS -o /dev/null -w '%{{http_code}}' --max-time {int(timeout)} {shlex.quote(url)} || true")


def http_ok(code: str) -> bool:
    return code.startswith(("2", "3"))


def compact(value: object, default: str = "-", limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return default
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def bool_word(value: bool) -> str:
    return "yes" if value else "no"


def state_mark(value: str, ok_values: set[str] | None = None) -> str:
    normalized = compact(value, default="unknown").lower()
    expected_ok = ok_values or {"active", "enabled", "primary", "standby", "streaming", "200"}
    if normalized in expected_ok or normalized.startswith(("2", "3")):
        return "OK"
    if normalized in {"inactive", "disabled", "absent", "no", "false", "-"}:
        return "OFF"
    return "CHECK"


def section(title: str, rows: list[str]) -> str:
    body = [row for row in rows if row is not None and row != ""]
    if not body:
        body = ["-"]
    return "\n".join([f"[{title}]", *body])


def kv_rows(items: list[tuple[str, object]], label_width: int = 15) -> list[str]:
    return [f"{label:<{label_width}} {compact(value)}" for label, value in items]


def service_row(name: str) -> str:
    state = compact(service_state(name), default="unknown", limit=32)
    enabled = compact(service_enabled(name), default="unknown", limit=32)
    mark = "OK" if state == "active" else ("OFF" if state == "inactive" else "CHECK")
    return f"{mark:<5} {name:<38} {state:<10} {enabled}"


def service_table(names: tuple[str, ...]) -> list[str]:
    return ["STATE SERVICE                                ACTIVE     ENABLED"] + [service_row(name) for name in names]


def trim_block(text: str, max_lines: int = 8, max_chars: int = 1200) -> str:
    lines = [line.rstrip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return "-"
    if len(lines) > max_lines:
        omitted = len(lines) - max_lines
        lines = lines[-max_lines:]
        lines.insert(0, f"... {omitted} earlier lines omitted")
    output = "\n".join(lines)
    if len(output) > max_chars:
        output = output[-max_chars:]
        output = "... " + output.lstrip()
    return output


def command_note(output: str) -> str:
    text = str(output or "").strip()
    if not text:
        return "-"
    if text.startswith("$ "):
        return trim_block(text, max_lines=5, max_chars=700)
    return trim_block(text, max_lines=8, max_chars=1000)


def http_line(label: str, url: str) -> str:
    code = compact(http_status(url), default="-", limit=16)
    mark = "OK" if http_ok(code) else "CHECK"
    return f"{mark:<5} {label:<18} {code}"


def ai_hosts() -> list[str]:
    raw_hosts = os.getenv("OLLAMA_HOSTS", "").strip()
    if raw_hosts:
        hosts = [item.strip().rstrip("/") for item in raw_hosts.split(",") if item.strip()]
    else:
        hosts = [(os.getenv("OLLAMA_HOST") or "http://wtr-linux:11434").strip().rstrip("/")]
    return hosts or ["http://wtr-linux:11434"]


def node_role() -> str:
    role_file = Path("/etc/mooncen-node-role")
    if not role_file.exists():
        return "unknown"
    value = role_file.read_text(encoding="utf-8", errors="ignore").strip().lower()
    return value or "unknown"


def last_failover_log(lines: int = 8) -> str:
    if not FAILOVER_LOG.exists():
        return "(no failover log)"
    return run(f"tail -n {int(lines)} {shlex.quote(str(FAILOVER_LOG))}")


def status_text() -> str:
    configured_role = node_role()
    local_role = db_role()
    receiver = run(
        "sudo -u postgres psql -Atqc "
        "\"SELECT status || '|' || coalesce(sender_host,'') || '|' || coalesce(slot_name,'') "
        "FROM pg_stat_wal_receiver;\""
    )
    cloud_health = run("pg_isready -h ${CLOUD_DB_HOST:-cloud} -p ${CLOUD_DB_PORT:-5432} -t 3 || true")
    env_override = crawler_db_override()
    return "\n\n".join(
        [
            section(
                "MoonCen Status",
                kv_rows(
                    [
                        ("node_role", configured_role),
                        ("local_db_role", local_role),
                        ("wal_receiver", receiver or "-"),
                        ("cloud_db", cloud_health),
                        ("auto_failover", bool_word(ENABLE_FILE.exists())),
                        ("fail_count", fail_count()),
                    ]
                ),
            ),
            section("Crawler", kv_rows([("env", crawler_env_summary() or env_override or "-")])),
            section(
                "Key Services",
                service_table(
                    (
                        "postgresql",
                        "nginx",
                        "mooncen-crawler",
                        "mooncen-staging-apply.timer",
                        "mooncen-api",
                        "mooncen-frontend",
                        "cloudflared",
                        "mooncen-ops-bot",
                    )
                ),
            ),
        ]
    )


def failover_status_text() -> str:
    return section(
        "Failover",
        kv_rows(
            [
                ("policy", "manual only"),
                ("auto_file", bool_word(ENABLE_FILE.exists())),
                ("fail_count", fail_count()),
                ("automatic", "disabled (legacy watcher retired)"),
                ("log_command", "/failover_log"),
            ]
        ),
    )


def failover_log_text() -> str:
    return section("Failover Log", command_note(last_failover_log(30)).splitlines())


def public_status_text() -> str:
    return section(
        "Public",
        [
            http_line("mooncen health", "https://mooncen.kr/health"),
            http_line("www health", "https://www.mooncen.kr/health"),
            http_line("mooncen root", "https://mooncen.kr/"),
        ],
    )


def replica_status_text() -> str:
    receiver = run(
        "sudo -u postgres psql -Atqc "
        "\"SELECT status || '|' || coalesce(sender_host,'') || '|' || coalesce(slot_name,'') "
        "FROM pg_stat_wal_receiver;\""
    )
    return section(
        "Replica",
        kv_rows(
            [
                ("node_role", node_role()),
                ("local_db_role", db_role()),
                ("wal_receiver", receiver or "-"),
                ("cloud_db", run("pg_isready -h ${CLOUD_DB_HOST:-cloud} -p ${CLOUD_DB_PORT:-5432} -t 3 || true")),
            ]
        ),
    )


def services_text() -> str:
    services = (
        "postgresql",
        "nginx",
        "mooncen-api",
        "mooncen-frontend",
        "mooncen-crawler",
        "mooncen-staging-apply.timer",
        "mooncen-ai-worker",
        "cloudflared",
        "mooncen-cloudflared-role-guard.timer",
        "mooncen-cloudflare-gate.timer",
        "mooncen-backup.timer",
        "mooncen-ops-bot",
    )
    return section("Services", service_table(services))


def cloudflare_status_text() -> str:
    return "\n\n".join(
        [
            section(
                "Cloudflare",
                kv_rows(
                    [
                        ("node_role", node_role()),
                        ("cloudflared", service_state("cloudflared")),
                        ("enabled", service_enabled("cloudflared")),
                        (
                            "role_guard",
                            service_state("mooncen-cloudflared-role-guard.timer", "mooncen-cloudflared-role-guard.service"),
                        ),
                        (
                            "gate_timer",
                            service_state("mooncen-cloudflare-gate.timer", "mooncen-cloudflare-gate.service"),
                        ),
                        ("gate_disabled", bool_word(GATE_DISABLE_FILE.exists())),
                    ]
                ),
            ),
            public_status_text(),
        ]
    )


def ai_status_text() -> str:
    hosts = ai_hosts()
    model = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")
    host_lines = []
    for host in hosts:
        tags_code = http_status(f"{host}/api/tags")
        models = run(
            "OLLAMA_PROBE_HOST="
            + shlex.quote(host)
            + " python3 - <<'PY'\n"
            "import json, os, urllib.request\n"
            "host=(os.getenv('OLLAMA_PROBE_HOST') or '').rstrip('/')\n"
            "try:\n"
            "    with urllib.request.urlopen(host + '/api/tags', timeout=8) as r:\n"
            "        payload=json.loads(r.read().decode('utf-8'))\n"
            "    print(', '.join(str(item.get('name') or item.get('model') or '') for item in payload.get('models', [])) or '-')\n"
            "except Exception as exc:\n"
            "    print(type(exc).__name__ + ': ' + str(exc))\n"
            "PY"
        )
        model_state = "present" if model in models else "missing"
        mark = "OK" if http_ok(tags_code) and model_state == "present" else "CHECK"
        host_lines.append(f"{mark:<5} {host:<28} http={tags_code or '-'} model={model_state} models={compact(models, limit=80)}")
    return "\n\n".join(
        [
            section(
                "AI Worker",
                kv_rows(
                    [
                        ("worker", service_state("mooncen-ai-worker")),
                        ("enabled", service_enabled("mooncen-ai-worker")),
                        ("model", model),
                    ]
                ),
            ),
            section("Ollama Hosts", host_lines),
        ]
    )


def backup_status_text() -> str:
    return "\n\n".join(
        [
            section(
                "Backup",
                kv_rows(
                    [
                        ("timer_service", service_state("mooncen-backup.timer", "mooncen-backup.service")),
                        ("enabled", service_enabled("mooncen-backup.timer")),
                    ]
                ),
            ),
            section("Recent Log", command_note(run("journalctl -u mooncen-backup.service --no-pager -n 20 || true")).splitlines()),
        ]
    )


def staging_status_text() -> str:
    return "\n\n".join(
        [
            section(
                "Crawler Staging",
                kv_rows(
                    [
                        ("timer_service", service_state("mooncen-staging-apply.timer", "mooncen-staging-apply.service")),
                        ("enabled", service_enabled("mooncen-staging-apply.timer")),
                        ("crawler_env", crawler_env_summary() or "-"),
                    ]
                ),
            ),
            section(
                "Recent Log",
                command_note(run("journalctl -u mooncen-staging-apply.service --no-pager -n 20 || true")).splitlines(),
            ),
        ]
    )


def summary_text() -> str:
    anomalies = collect_anomalies()
    health = ["OK no active anomalies"] if not anomalies else [f"CHECK {item}" for item in anomalies[:8]]
    if len(anomalies) > 8:
        health.append(f"... {len(anomalies) - 8} more")
    return "\n\n".join(
        [
            section(
                "Summary",
                kv_rows(
                    [
                        ("node_role", node_role()),
                        ("db_role", db_role()),
                        ("crawler", service_state("mooncen-crawler")),
                        ("staging_timer", service_state("mooncen-staging-apply.timer")),
                        ("cloudflared", service_state("cloudflared")),
                        ("ops_bot", service_state("mooncen-ops-bot")),
                    ]
                ),
            ),
            public_status_text(),
            section("Health", health),
            section("Details", ["/status /services /replica_status /cloudflare_status", "/crawler_status /staging_status /ai_status /backup_status"]),
        ]
    )


def monitoring_text() -> str:
    anomalies = collect_anomalies()
    return "\n\n".join(
        [
            section("Monitoring", ["OK no active anomalies"] if not anomalies else [f"CHECK {item}" for item in anomalies]),
            status_text(),
            public_status_text(),
            section("Gate", kv_rows([("disabled_file", bool_word(GATE_DISABLE_FILE.exists()))])),
        ]
    )


def operations_text() -> str:
    return "\n\n".join(
        [
            section(
                "Operations",
                [
                    "/summary            compact summary",
                    "/monitoring         anomalies + key status",
                    "/status             local node detail",
                    "/services           service table",
                    "/public_status      public endpoint health",
                    "/replica_status     PostgreSQL replica",
                    "/cloudflare_status  tunnel and role guard",
                    "/crawler_status     crawler state and short log",
                    "/staging_status     staging/apply state",
                    "/ai_status          AI worker and Ollama nodes",
                    "/backup_status      backup timer and short log",
                    "/failover_status    manual failover state, no log",
                    "/failover_log       failover log on demand",
                    "/manual_failover    manual failover guide",
                ],
            ),
            section("Disabled Controls", ["/crawler_restart /failover_enable /failover_disable /promote_n100"]),
        ]
    )


def crawler_status_text() -> str:
    running = run("pgrep -af 'run_crawlers.py' | grep -v 'pgrep -af' | head -n 3 || true")
    logs = run("journalctl -u mooncen-crawler --no-pager -n 12 || true")
    return "\n\n".join(
        [
            section(
                "Crawler",
                kv_rows(
                    [
                        ("state", service_state("mooncen-crawler")),
                        ("enabled", service_enabled("mooncen-crawler")),
                        ("env", crawler_env_summary() or crawler_db_override() or "-"),
                    ]
                ),
            ),
            section("Running", command_note(running).splitlines()),
            section("Recent Log", command_note(logs).splitlines()),
        ]
    )


def manual_failover_text() -> str:
    return section(
        "Manual Failover Procedure",
        [
            "1. Confirm cloud is unavailable and n100 DB is standby.",
            "2. On n100: sudo /opt/mooncen/deploy/ha/postgres_promote_standby.sh",
            "3. Start n100 app services manually.",
            "4. Move Cloudflare Public Hostnames/DNS tunnel to n100 manually.",
            "5. Before returning to cloud, rebuild the old primary from the new primary.",
            "Note: this bot does not execute promotion or tunnel changes.",
        ],
    )


def disabled_control_text(command: str) -> str:
    return section(
        "Disabled",
        [
            f"command: {command}",
            "policy: manual DB promotion and manual Cloudflare tunnel switch",
        ],
    )


def collect_anomalies() -> list[str]:
    anomalies: list[str] = []
    role = node_role()
    local_db = db_role()
    services = {
        "postgresql": service_state("postgresql"),
        "crawler": service_state("mooncen-crawler"),
        "api": service_state("mooncen-api"),
        "frontend": service_state("mooncen-frontend"),
        "cloudflared": service_state("cloudflared"),
        "ops_bot": service_state("mooncen-ops-bot"),
        "staging_timer": service_state("mooncen-staging-apply.timer"),
    }
    if services["postgresql"] != "active":
        anomalies.append(f"postgresql not active: {services['postgresql']}")
    for label, url in (
        ("mooncen.kr", "https://mooncen.kr/health"),
        ("www.mooncen.kr", "https://www.mooncen.kr/health"),
    ):
        code = http_status(url)
        if not http_ok(code):
            anomalies.append(f"public {label} health check failed: {code or 'no response'}")
    ai_codes = {host: http_status(f"{host}/api/tags") for host in ai_hosts()}
    if ai_codes and not any(http_ok(code) for code in ai_codes.values()):
        details = ", ".join(f"{host}={code or 'no response'}" for host, code in ai_codes.items())
        anomalies.append(f"all AI nodes unreachable: {details}")
    if role == "standby":
        if local_db != "standby":
            anomalies.append(f"standby node DB role is {local_db}")
        receiver = run(
            "sudo -u postgres psql -Atqc "
            "\"SELECT status FROM pg_stat_wal_receiver LIMIT 1;\""
        )
        if receiver and receiver != "streaming":
            anomalies.append(f"WAL receiver not streaming: {receiver}")
        if services["crawler"] != "active":
            anomalies.append(f"crawler not active on standby crawler node: {services['crawler']}")
        crawler_env = crawler_env_summary()
        if "CRAWL_WRITE_MODE=staging" not in crawler_env:
            anomalies.append(f"crawler staging write mode missing on standby node: {crawler_env or '-'}")
        if services["staging_timer"] != "active":
            anomalies.append(f"staging apply timer not active on standby crawler node: {services['staging_timer']}")
        if services["cloudflared"] == "active":
            anomalies.append("cloudflared active on standby node")
        if ENABLE_FILE.exists():
            anomalies.append("automatic failover enable file exists")
    elif role == "primary":
        if local_db != "primary":
            anomalies.append(f"primary node DB role is {local_db}")
        for key in ("api", "frontend", "cloudflared"):
            if services[key] != "active":
                anomalies.append(f"{key} not active on primary: {services[key]}")
    else:
        anomalies.append(f"unknown node role: {role}")
    return anomalies


def handle_command(text: str, chat_id: str) -> str:
    command = text.strip()
    normalized = command.lower()
    if normalized in MENU_ALIASES:
        return operations_text()
    if normalized in TEXT_COMMAND_ALIASES:
        command = TEXT_COMMAND_ALIASES[normalized]
    base = command.split(maxsplit=1)[0].split("@", 1)[0].lower()
    if base in {"/start", "/help"}:
        return operations_text()
    if base == "/status":
        return status_text()
    if base in {"/menu", "/operations", "/operation"}:
        return operations_text()
    if base == "/summary":
        return summary_text()
    if base in {"/monitoring", "/monitor"}:
        return monitoring_text()
    if base == "/services":
        return services_text()
    if base == "/public_status":
        return public_status_text()
    if base == "/replica_status":
        return replica_status_text()
    if base == "/cloudflare_status":
        return cloudflare_status_text()
    if base == "/ai_status":
        return ai_status_text()
    if base == "/backup_status":
        return backup_status_text()
    if base == "/staging_status":
        return staging_status_text()
    if base == "/failover_status":
        return failover_status_text()
    if base == "/failover_log":
        return failover_log_text()
    if base in {"/manual_failover", "/failover_guide"}:
        return manual_failover_text()
    if base in {"/failover_enable", "/failover_disable", "/promote_n100"}:
        return disabled_control_text(base)
    if base == "/crawler_status":
        return crawler_status_text()
    if base == "/crawler_restart":
        return disabled_control_text(base)
    return "unknown command. use /help"


def load_state() -> dict:
    if not BOT_STATE_FILE.exists():
        return {"offset": 0, "log_size": 0}
    try:
        return json.loads(BOT_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"offset": 0, "log_size": 0}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    BOT_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def poll_updates(offset: int) -> list[dict]:
    data = telegram(
        "getUpdates",
        {"offset": offset, "timeout": POLL_TIMEOUT, "allowed_updates": ["message"]},
        timeout=POLL_TIMEOUT + 10,
    )
    if not data.get("ok"):
        return []
    return data.get("result") or []


def monitor_failover_log(state: dict) -> None:
    now = time.monotonic()
    if now - float(state.get("last_monitor_at", 0)) < MONITOR_INTERVAL:
        return
    state["last_monitor_at"] = now
    if not FAILOVER_LOG.exists():
        return
    size = FAILOVER_LOG.stat().st_size
    old_size = int(state.get("log_size") or 0)
    if old_size <= 0 or size < old_size:
        state["log_size"] = size
        return
    if size == old_size:
        return
    with FAILOVER_LOG.open("rb") as handle:
        handle.seek(old_size)
        chunk = handle.read(6000).decode("utf-8", errors="replace").strip()
    state["log_size"] = size
    if not chunk:
        return
    if any(marker in chunk for marker in ("cloud health failed", "promoting", "promoted", "completed", "disabled")):
        broadcast(section("Failover Log Update", command_note(chunk).splitlines()))


def monitor_anomalies(state: dict) -> None:
    now = time.monotonic()
    if now - float(state.get("last_anomaly_check_at", 0)) < max(MONITOR_INTERVAL, 30):
        return
    state["last_anomaly_check_at"] = now
    anomalies = collect_anomalies()
    signature = "\n".join(sorted(anomalies))
    previous = str(state.get("anomaly_signature") or "")
    if signature == previous:
        return
    state["anomaly_signature"] = signature
    if anomalies:
        broadcast(section("Abnormal State", [f"CHECK {item}" for item in anomalies]))
    elif previous:
        broadcast(section("Abnormal State", ["OK cleared"]) + "\n\n" + summary_text())


def main() -> int:
    if not TOKEN:
        print("MOONCEN_BOT_TOKEN is not set. Bot fails closed.", file=sys.stderr)
        return 78
    if not CHAT_IDS:
        print("MOONCEN_BOT_CHAT_ID(S) is not set. Bot fails closed.", file=sys.stderr)
        return 78
    state = load_state()
    if STARTUP_MESSAGE:
        broadcast(section("Ops Bot", ["started"]) + "\n\n" + summary_text())
    while True:
        try:
            updates = poll_updates(int(state.get("offset") or 0))
            for update in updates:
                state["offset"] = int(update["update_id"]) + 1
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                chat_id = str(chat.get("id") or "")
                text = str(message.get("text") or "")
                if not chat_id or not is_command_text(text):
                    continue
                if not is_authorized(chat_id):
                    send_message(chat_id, "unauthorized")
                    continue
                reply = handle_command(text, chat_id)
                keyboard = operations_keyboard() if should_show_menu_keyboard(text) else None
                send_message(chat_id, reply, reply_markup=keyboard)
            if FAILOVER_LOG_ALERTS:
                monitor_failover_log(state)
            monitor_anomalies(state)
            save_state(state)
        except requests.RequestException as exc:
            print(f"telegram request failed: {type(exc).__name__}", file=sys.stderr)
            time.sleep(5)
        except subprocess.TimeoutExpired as exc:
            print(f"command timeout: {exc}", file=sys.stderr)
            time.sleep(2)
        except Exception as exc:  # noqa: BLE001
            print(f"bot loop error: {exc}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
