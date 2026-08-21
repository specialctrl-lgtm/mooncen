import requests
import json
from datetime import datetime, timezone, timedelta
import time
import subprocess
import os
import ipaddress
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ==========================================
# [설정] 아래 정보를 입력하세요
# ==========================================

TS_TAILNET = "dinosaur-piano.ts.net"
TS_CLIENT_ID = "kmje7NsCGJ11CNTRL"
TS_CLIENT_SECRET = "tskey-client-kmje7NsCGJ11CNTRL-ajXtWv7odZ2ZoNEtPycPZ22jWkf8LdAw"

TG_BOT_TOKEN = "8367370868:AAH825S7IMh-A-FJ-kwqGTSXl2R9_ZOsQcA"
TG_CHAT_ID = "8350243713"

OFFLINE_THRESHOLD_MIN = 15
AUTO_CHECK_INTERVAL = 60

RESOURCE_CHECK_INTERVAL = 60
RESOURCE_ALERT_INTERVAL = 3600
RESOURCE_ALERT_AFTER_COUNT = 10
RESOURCE_CPU_THRESHOLD = 80.0
PROMETHEUS_METRICS_PORT = int(os.environ.get("TAILSCALE_MON_METRICS_PORT", "9108"))
PROMETHEUS_URL = os.environ.get("TAILSCALE_MON_PROMETHEUS_URL", "http://localhost:9090")
TELEGRAM_ENABLED = os.environ.get("TAILSCALE_MON_TELEGRAM_ENABLED", "0").lower() in ("1", "true", "yes", "on")
INTERNAL_ALERTS_ENABLED = os.environ.get("TAILSCALE_MON_INTERNAL_ALERTS_ENABLED", "0").lower() in ("1", "true", "yes", "on")
MOONCEN_OPS_STATUS_URLS = [
    url.strip()
    for url in os.environ.get(
        "MOONCEN_OPS_STATUS_URLS",
        "http://localhost:8765/api/mobile-status,http://gen1web:8765/api/mobile-status,http://cloud:8765/api/mobile-status",
    ).split(",")
    if url.strip()
]
QUIET_START_HOUR = 22
QUIET_END_HOUR = 7
KST = timezone(timedelta(hours=9))

# Tailscale SSH resource monitoring targets.
# Add more Linux devices after enabling Tailscale SSH on them:
#   sudo tailscale set --ssh
RESOURCE_SSH_TARGETS = [
    {"name": "bot", "host": "localhost", "user": "ubuntu", "local": True},
]

RESOURCE_SSH_USERS = {
    "ds1515": "sgm",
    "ds718": "sgm",
    "hp-proxmox": "root",
    "wtr-nas": "sgm",
    "wtr-proxmox": "root",
}

SHUTDOWN_ALLOWED_HOSTS = {
    "ds1515",
    "ds718",
    "hp-proxmox",
    "wtr-nas",
    "wtr-proxmox",
}
SHUTDOWN_CONFIRM_TTL = 60

WOL_TARGETS = {
    "wtr-nas": {
        "mac": "00:11:32:90:1b:1c",
        "relay": "wtr-proxmox",
        "relay_user": "root",
        "broadcast": "192.168.0.255",
        "port": 9,
    },
}
WOL_DB_PATH = "/home/ubuntu/tailscale_mon/wol_targets.json"
MANAGE_DB_PATH = "/home/ubuntu/tailscale_mon/manage_targets.json"

# ==========================================

# 전역 변수
device_status_history = {} 
resource_alert_state = {}
resource_metrics_cache = {}
shutdown_confirmations = {}
reboot_confirmations = {}
command_selection = {}

# 토큰 캐싱 변수
cached_token = None
token_expiry_time = 0

def get_access_token():
    global cached_token, token_expiry_time
    
    if cached_token and time.time() < token_expiry_time - 300:
        return cached_token

    url = "https://api.tailscale.com/api/v2/oauth/token"
    data = {"client_id": TS_CLIENT_ID, "client_secret": TS_CLIENT_SECRET, "grant_type": "client_credentials"}
    try:
        resp = requests.post(url, data=data, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        
        cached_token = data['access_token']
        expires_in = data.get('expires_in', 3600) 
        token_expiry_time = time.time() + expires_in
        
        print(f"🔑 새 토큰 발급 완료")
        return cached_token
    except Exception as e:
        print(f"[Error] 토큰 발급 실패: {e}")
        return None

def send_telegram(text, reply_markup=None):
    if not TELEGRAM_ENABLED:
        return
    if not text: return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TG_CHAT_ID, "text": text}
    if reply_markup is not None:
        data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    try:
        requests.post(url, data=data, timeout=5)
    except Exception as e:
        print(f"❌ 텔레그램 전송 에러: {e}")

def is_quiet_hours(now=None):
    now = now or datetime.now(KST)
    hour = now.hour
    if QUIET_START_HOUR > QUIET_END_HOUR:
        return hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR
    return QUIET_START_HOUR <= hour < QUIET_END_HOUR

def fetch_resource_metrics(target):
    remote_script = r"""
import json
import glob
import os
import shutil
import time

def read_cpu():
    with open("/proc/stat", "r", encoding="utf-8") as f:
        parts = f.readline().split()[1:]
    return [int(x) for x in parts]

def read_mem_percent():
    values = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as f:
        for line in f:
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0])
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    if not total:
        return 0.0
    return round((total - available) * 100 / total, 1)

def read_temperature_c():
    readings = []
    paths = glob.glob("/sys/class/hwmon/hwmon*/temp*_input")
    paths += glob.glob("/sys/class/thermal/thermal_zone*/temp")
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read().strip()
            value = float(raw)
        except Exception:
            continue
        if value > 1000:
            value = value / 1000
        if -20 <= value <= 130:
            readings.append(value)
    if not readings:
        return None
    return round(max(readings), 1)

a = read_cpu()
time.sleep(1)
b = read_cpu()
idle_delta = (b[3] + b[4]) - (a[3] + a[4])
total_delta = sum(b) - sum(a)
cpu_percent = 0.0 if total_delta <= 0 else round((1 - idle_delta / total_delta) * 100, 1)
disk = shutil.disk_usage("/")
load1, load5, load15 = os.getloadavg()

print(json.dumps({
    "cpu_percent": cpu_percent,
    "memory_percent": read_mem_percent(),
    "disk_percent": round(disk.used * 100 / disk.total, 1),
    "load1": round(load1, 2),
    "load5": round(load5, 2),
    "load15": round(load15, 2),
    "temperature_c": read_temperature_c(),
}))
"""
    if target.get("local"):
        cmd = ["python3", "-c", remote_script]
    else:
        host = target["host"]
        user = target["user"]
        remote_cmd = "python3 - <<'PY'\n" + remote_script + "\nPY"
        cmd = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=8",
            f"{user}@{host}",
            remote_cmd,
        ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"ssh exit {result.returncode}")
    return json.loads(result.stdout)

def format_resource_line(name, metrics):
    state = resource_alert_state.get(name, {})
    high_count = state.get("high_count", 0)
    return (
        f"{name}: CPU {metrics['cpu_percent']}%, "
        f"MEM {metrics['memory_percent']}%, "
        f"DISK {metrics['disk_percent']}%, "
        f"LOAD {metrics['load1']}/{metrics['load5']}/{metrics['load15']}, "
        f"CPU>{RESOURCE_CPU_THRESHOLD:.0f}% 연속 {high_count}/{RESOURCE_ALERT_AFTER_COUNT}"
    )

def get_resource_report():
    lines = []
    for target in RESOURCE_SSH_TARGETS:
        name = target["name"]
        try:
            metrics = fetch_resource_metrics(target)
            update_resource_metrics_cache(name, metrics)
            lines.append("🖥 " + format_resource_line(name, metrics))
        except Exception as e:
            lines.append(f"⚠️ {name}: resource check failed ({e})")

    if not lines:
        return "⚠️ 리소스 모니터링 대상이 없습니다."
    return "📈 **[리소스 상태]**\n\n" + "\n".join(lines)

def format_resource_line(name, metrics):
    return (
        f"{name}: "
        f"{format_usage('C', metrics['cpu_percent'])} "
        f"{format_usage('M', metrics['memory_percent'])} "
        f"{format_usage('D', metrics['disk_percent'])} "
        f"{format_temperature(metrics)}"
    )

def usage_icon(value):
    value = float(value)
    if value <= 40:
        return "🟢"
    if value <= 70:
        return "🟡"
    return "🔴"

def format_usage(label, value):
    return f"{usage_icon(value)}{label}{float(value):.0f}%"

def format_temperature(metrics):
    value = metrics.get("temperature_c")
    if value is None:
        return "T-"
    return f"T{float(value):.0f}C"

def update_resource_metrics_cache(name, metrics):
    resource_metrics_cache[name] = {
        **metrics,
        "last_update": time.time(),
    }

def prometheus_label_value(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

def build_prometheus_metrics():
    metric_defs = [
        ("tailscale_resource_cpu_percent", "gauge", "CPU usage percent", "cpu_percent"),
        ("tailscale_resource_memory_percent", "gauge", "Memory usage percent", "memory_percent"),
        ("tailscale_resource_disk_percent", "gauge", "Disk usage percent", "disk_percent"),
        ("tailscale_resource_load1", "gauge", "1 minute load average", "load1"),
        ("tailscale_resource_load5", "gauge", "5 minute load average", "load5"),
        ("tailscale_resource_load15", "gauge", "15 minute load average", "load15"),
        ("tailscale_resource_temperature_celsius", "gauge", "Highest readable temperature in Celsius", "temperature_c"),
        ("tailscale_resource_last_update_timestamp_seconds", "gauge", "Last successful resource scrape timestamp", "last_update"),
    ]
    lines = []
    for metric_name, metric_type, help_text, _ in metric_defs:
        lines.append(f"# HELP {metric_name} {help_text}")
        lines.append(f"# TYPE {metric_name} {metric_type}")

    for name, metrics in sorted(resource_metrics_cache.items()):
        labels = f'name="{prometheus_label_value(name)}"'
        for metric_name, _, _, key in metric_defs:
            value = metrics.get(key)
            if value is None:
                continue
            lines.append(f"{metric_name}{{{labels}}} {float(value)}")
    lines.extend(build_prometheus_target_metrics())
    lines.extend(build_node_summary_metrics())
    return "\n".join(lines) + "\n"

def prometheus_query(query):
    try:
        resp = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=3,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            return []
        return data.get("data", {}).get("result", [])
    except Exception as e:
        print(f"Prometheus query failed: {e}")
        return []

def build_prometheus_target_metrics():
    lines = [
        "# HELP mooncen_scrape_target_up Prometheus scrape target health with last error label",
        "# TYPE mooncen_scrape_target_up gauge",
    ]
    try:
        resp = requests.get(f"{PROMETHEUS_URL}/api/v1/targets", params={"state": "any"}, timeout=3)
        resp.raise_for_status()
        targets = resp.json().get("data", {}).get("activeTargets", [])
    except Exception as e:
        print(f"Prometheus targets query failed: {e}")
        return lines

    for target in targets:
        labels = target.get("labels", {})
        discovered = target.get("discoveredLabels", {})
        node = labels.get("node") or discovered.get("node") or ""
        job = labels.get("job") or discovered.get("job") or ""
        instance = labels.get("instance") or discovered.get("__address__") or ""
        role = labels.get("role") or discovered.get("role") or ""
        alerting = labels.get("alerting") or discovered.get("alerting") or ""
        health = target.get("health") or "unknown"
        last_error = target.get("lastError") or ""
        value = 1 if health == "up" else 0
        metric_labels = ",".join([
            f'node="{prometheus_label_value(node)}"',
            f'job="{prometheus_label_value(job)}"',
            f'instance="{prometheus_label_value(instance)}"',
            f'role="{prometheus_label_value(role)}"',
            f'alerting="{prometheus_label_value(alerting)}"',
            f'health="{prometheus_label_value(health)}"',
            f'last_error="{prometheus_label_value(last_error)}"',
        ])
        lines.append(f"mooncen_scrape_target_up{{{metric_labels}}} {value}")
    return lines

def format_summary_number(value, suffix="", decimals=1):
    if value is None:
        return "-"
    try:
        return f"{float(value):.{decimals}f}{suffix}"
    except Exception:
        return "-"

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

def query_values_by_node(query):
    values = {}
    for item in prometheus_query(query):
        node = item.get("metric", {}).get("node")
        if not node:
            continue
        values[node] = item.get("value", [None, None])[1]
    return values

def build_node_summary_metrics():
    lines = [
        "# HELP mooncen_node_summary_info Current server summary for monitoring clients",
        "# TYPE mooncen_node_summary_info gauge",
    ]
    up_items = prometheus_query('up{node!="",job=~"node_exporter|windows_exporter"}')
    nodes = {}
    for item in up_items:
        metric = item.get("metric", {})
        node = metric.get("node")
        if not node:
            continue
        current = nodes.setdefault(node, {"node": node})
        if current.get("job") not in ("node_exporter", "windows_exporter"):
            current.update({
                "role": metric.get("role", ""),
                "alerting": metric.get("alerting", ""),
                "job": metric.get("job", ""),
                "up": item.get("value", [None, "0"])[1],
            })

    queries = {
        "cpu": '100 - (avg by (node) (rate(node_cpu_seconds_total{mode="idle",node!=""}[5m])) * 100) or 100 - (avg by (node) (rate(windows_cpu_time_total{mode="idle",node!=""}[5m])) * 100)',
        "mem": '(1 - (node_memory_MemAvailable_bytes{node!=""} / node_memory_MemTotal_bytes{node!=""})) * 100 or (1 - (windows_memory_available_bytes{node!=""} / windows_memory_physical_total_bytes{node!=""})) * 100',
        "disk": '100 - ((node_filesystem_avail_bytes{mountpoint="/",fstype!="rootfs",node!=""} * 100) / node_filesystem_size_bytes{mountpoint="/",fstype!="rootfs",node!=""}) or 100 - ((windows_logical_disk_free_bytes{volume="C:",node!=""} * 100) / windows_logical_disk_size_bytes{volume="C:",node!=""})',
        "temp": 'max by (node) (node_hwmon_temp_celsius{job="node_exporter",node!=""}) or max by (node) (node_thermal_temperature_celsius{job="node_exporter",node!=""}) or max by (node) (label_replace(tailscale_resource_temperature_celsius, "node", "$1", "name", "(.*)"))',
        "uptime": 'time() - node_boot_time_seconds{node!=""} or time() - windows_system_boot_time_timestamp{node!=""}',
    }
    values = {key: query_values_by_node(query) for key, query in queries.items()}

    for node in sorted(nodes):
        item = nodes[node]
        label_pairs = {
            "node": node,
            "up": "UP" if str(item.get("up")) == "1" else "DOWN",
            "cpu": format_summary_number(values["cpu"].get(node), "%"),
            "mem": format_summary_number(values["mem"].get(node), "%"),
            "disk": format_summary_number(values["disk"].get(node), "%"),
            "temp": format_summary_number(values["temp"].get(node), "C", 0),
            "uptime": format_uptime(values["uptime"].get(node)),
            "role": item.get("role", ""),
            "alerting": item.get("alerting", ""),
        }
        metric_labels = ",".join(
            f'{key}="{prometheus_label_value(value)}"' for key, value in label_pairs.items()
        )
        lines.append(f"mooncen_node_summary_info{{{metric_labels}}} 1")
    return lines

class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/metrics", "/metrics/"):
            self.send_response(404)
            self.end_headers()
            return

        body = build_prometheus_metrics().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return

def start_prometheus_metrics_server():
    if PROMETHEUS_METRICS_PORT <= 0:
        return

    def serve():
        server = ThreadingHTTPServer(("0.0.0.0", PROMETHEUS_METRICS_PORT), MetricsHandler)
        print(f"Prometheus metrics listening on :{PROMETHEUS_METRICS_PORT}/metrics")
        server.serve_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

def classify_resource_error(error):
    text = str(error)
    if "connect to host" in text and "port 22: Connection refused" in text:
        return "SSH 꺼짐"
    if "Permission denied" in text:
        return "SSH 권한 없음"
    if "Could not resolve hostname" in text:
        return "DNS 실패"
    if "Operation timed out" in text or "Connection timed out" in text:
        return "SSH 시간초과"
    if "python3: command not found" in text:
        return "python3 없음"
    return "조회 실패"

def get_tailscale_node_name(node):
    dns_name = (node.get("DNSName") or "").strip(".")
    if dns_name:
        return dns_name.split(".")[0]
    return node.get("HostName") or "unknown"

def get_tailscale_nodes():
    result = subprocess.run(
        ["tailscale", "status", "--json"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"tailscale exit {result.returncode}")

    status = json.loads(result.stdout)
    nodes_by_key = {}

    def node_key(node):
        return f"name:{get_tailscale_node_name(node).lower()}"

    def add_node(node, is_self):
        item = {**node, "_self": is_self}
        key = node_key(item)
        current = nodes_by_key.get(key)
        if not current:
            nodes_by_key[key] = item
            return
        if item.get("_self") or (item.get("Online") and not current.get("Online")):
            nodes_by_key[key] = {**current, **item}

    self_node = status.get("Self")
    if self_node:
        add_node(self_node, True)

    for peer in status.get("Peer", {}).values():
        add_node(peer, False)

    nodes = list(nodes_by_key.values())
    return sorted(nodes, key=lambda n: get_tailscale_node_name(n).lower())

def make_resource_target_from_node(node):
    name = get_tailscale_node_name(node)
    if node.get("_self"):
        return {"name": name, "host": "localhost", "user": "ubuntu", "local": True}
    return {"name": name, "host": name, "user": RESOURCE_SSH_USERS.get(name, "ubuntu")}

def get_resource_report():
    try:
        nodes = get_tailscale_nodes()
    except Exception as e:
        return f"Tailscale 목록 조회 실패: {e}"

    lines = []
    skipped_offline = 0
    skipped_unsupported = 0
    failed = 0
    for node in nodes:
        name = get_tailscale_node_name(node)
        os_name = node.get("OS") or "unknown"
        online = bool(node.get("Online"))

        if not online:
            skipped_offline += 1
            continue

        if os_name != "linux":
            skipped_unsupported += 1
            continue

        try:
            metrics = fetch_resource_metrics(make_resource_target_from_node(node))
            update_resource_metrics_cache(name, metrics)
            lines.append(
                f"{name:<14} "
                f"{format_usage('C', metrics['cpu_percent'])} "
                f"{format_usage('M', metrics['memory_percent'])} "
                f"{format_usage('D', metrics['disk_percent'])} "
                f"{format_temperature(metrics)}"
            )
        except Exception as e:
            failed += 1
            print(f"성능 조회 실패 ({name}): {classify_resource_error(e)} - {e}")

    if not lines:
        return "성능 조회 가능한 온라인 Linux 기기가 없습니다."

    summary = (
        f"\n\n제외: offline {skipped_offline}대, "
        f"미지원 {skipped_unsupported}대, 조회 실패 {failed}대"
    )
    return "[Tailscale 성능]\n\n" + "\n".join(lines) + summary

def check_resource_alerts():
    global resource_alert_state
    alert_messages = []
    quiet = is_quiet_hours()

    for target in RESOURCE_SSH_TARGETS:
        name = target["name"]
        try:
            metrics = fetch_resource_metrics(target)
            update_resource_metrics_cache(name, metrics)
        except Exception as e:
            print(f"리소스 체크 실패 ({name}): {e}")
            continue

        cpu = float(metrics["cpu_percent"])
        state = resource_alert_state.setdefault(name, {"high": False, "last_alert_at": 0, "high_count": 0})
        now_ts = time.time()

        if cpu >= RESOURCE_CPU_THRESHOLD:
            state["high"] = True
            state["high_count"] = state.get("high_count", 0) + 1
            enough_consecutive_checks = state["high_count"] >= RESOURCE_ALERT_AFTER_COUNT
            should_alert = (
                enough_consecutive_checks
                and now_ts - state.get("last_alert_at", 0) >= RESOURCE_ALERT_INTERVAL
            )
            if should_alert and not quiet:
                alert_messages.append(
                    f"🔥 **{name}** CPU {cpu}% "
                    f"({state['high_count']}회 연속, MEM {metrics['memory_percent']}%, DISK {metrics['disk_percent']}%)"
                )
                state["last_alert_at"] = now_ts
        else:
            if state["high"]:
                print(f"CPU 정상화 ({name}): {cpu}% after {state.get('high_count', 0)} high checks")
            state["high"] = False
            state["high_count"] = 0
            state["last_alert_at"] = 0

    if INTERNAL_ALERTS_ENABLED and alert_messages:
        send_telegram("🚨 **[CPU 사용량 경고]**\n\n" + "\n".join(alert_messages))
        print(f"CPU 알림 전송함: {len(alert_messages)}건")

def check_resource_alerts():
    global resource_alert_state
    alert_messages = []
    quiet = is_quiet_hours()

    try:
        nodes = get_tailscale_nodes()
    except Exception as e:
        print(f"리소스 대상 조회 실패: {e}")
        return

    for node in nodes:
        name = get_tailscale_node_name(node)
        if not node.get("Online") or node.get("OS") != "linux":
            continue

        try:
            metrics = fetch_resource_metrics(make_resource_target_from_node(node))
            update_resource_metrics_cache(name, metrics)
        except Exception as e:
            print(f"리소스 체크 실패 ({name}): {classify_resource_error(e)} - {e}")
            continue

        cpu = float(metrics["cpu_percent"])
        state = resource_alert_state.setdefault(name, {"high": False, "last_alert_at": 0, "high_count": 0})
        now_ts = time.time()

        if cpu >= RESOURCE_CPU_THRESHOLD:
            state["high"] = True
            state["high_count"] = state.get("high_count", 0) + 1
            should_alert = (
                state["high_count"] >= RESOURCE_ALERT_AFTER_COUNT
                and now_ts - state.get("last_alert_at", 0) >= RESOURCE_ALERT_INTERVAL
            )
            if should_alert and not quiet:
                alert_messages.append(f"{usage_icon(cpu)} **{name}** CPU {cpu}% ({state['high_count']}회 연속)")
                state["last_alert_at"] = now_ts
        else:
            if state["high"]:
                print(f"CPU 정상화 ({name}): {cpu}% after {state.get('high_count', 0)} high checks")
            state["high"] = False
            state["high_count"] = 0
            state["last_alert_at"] = 0

    if INTERNAL_ALERTS_ENABLED and alert_messages:
        send_telegram("**[CPU 사용량 경고]**\n\n" + "\n".join(alert_messages))
        print(f"CPU 알림 전송함: {len(alert_messages)}건")

def get_shutdown_target(host):
    host = host.strip().lower()
    if host not in SHUTDOWN_ALLOWED_HOSTS:
        return None
    return {"name": host, "host": host, "user": RESOURCE_SSH_USERS.get(host, "ubuntu")}

def get_shutdown_list_message():
    names = sorted(SHUTDOWN_ALLOWED_HOSTS)
    return "[종료 가능 서버]\n\n" + "\n".join(f"- {name}" for name in names)

def request_shutdown_confirmation(host):
    target = get_shutdown_target(host)
    if not target:
        return f"종료 허용 목록에 없는 서버입니다: {host}\n\n종료목록 으로 확인하세요."

    code = str(int(time.time() * 1000) % 9000 + 1000)
    shutdown_confirmations[target["name"]] = {
        "code": code,
        "expires_at": time.time() + SHUTDOWN_CONFIRM_TTL,
    }
    return (
        f"{target['name']} 서버 종료 확인\n\n"
        f"정말 종료하려면 {SHUTDOWN_CONFIRM_TTL}초 안에 아래 4자리 코드만 입력하세요.\n"
        f"{code}"
    )

def execute_remote_shutdown(target):
    remote_cmd = (
        "if [ \"$(id -u)\" = \"0\" ]; then "
        "(/usr/bin/systemctl poweroff -i --no-wall || /bin/systemctl poweroff -i --no-wall || /usr/sbin/poweroff -f || /sbin/poweroff -f || /usr/bin/systemctl poweroff --force --force || /bin/systemctl poweroff --force --force || /usr/sbin/shutdown -h now || /sbin/shutdown -h now); "
        "else "
        "(sudo -n /usr/syno/sbin/synopoweroff || sudo -n /usr/sbin/poweroff || sudo -n /sbin/poweroff || sudo -n /usr/sbin/shutdown -h now || sudo -n /sbin/shutdown -h now); "
        "fi"
    )
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=8",
        f"{target['user']}@{target['host']}",
        remote_cmd,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode not in (0, 255):
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"ssh exit {result.returncode}")
    return result.stdout.strip() or result.stderr.strip() or "shutdown command sent"

def confirm_shutdown(host, code):
    target = get_shutdown_target(host)
    if not target:
        return f"종료 허용 목록에 없는 서버입니다: {host}"

    pending = shutdown_confirmations.get(target["name"])
    if not pending:
        return f"{target['name']} 종료 확인 요청이 없습니다."
    if time.time() > pending["expires_at"]:
        shutdown_confirmations.pop(target["name"], None)
        return f"{target['name']} 종료 확인 시간이 만료됐습니다."
    if code.strip() != pending["code"]:
        return f"{target['name']} 종료 확인 코드가 틀렸습니다."

    shutdown_confirmations.pop(target["name"], None)
    try:
        result = execute_remote_shutdown(target)
        return f"{target['name']} 종료 명령 전송 완료\n{result}"
    except Exception as e:
        return f"{target['name']} 종료 명령 실패: {e}"

def handle_shutdown_command(text):
    parts = text.strip().split()
    if not parts:
        return None

    if parts[0] in ["종료목록", "/shutdowns"]:
        return get_shutdown_list_message()

    if parts[0] in ["종료", "/shutdown"]:
        if len(parts) != 2:
            return "사용법: 종료 서버명"
        return request_shutdown_confirmation(parts[1])

    if parts[0] in ["확인", "/confirm"]:
        if len(parts) != 3:
            return "사용법: 확인 서버명 코드"
        return confirm_shutdown(parts[1], parts[2])

    return None

def execute_remote_reboot(target):
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=8",
        f"{target['user']}@{target['host']}",
        "sudo -n /sbin/reboot || sudo -n /usr/sbin/reboot || /sbin/reboot || /usr/sbin/reboot",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode not in (0, 255):
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"ssh exit {result.returncode}")
    return result.stdout.strip() or result.stderr.strip() or "reboot command sent"

def request_reboot_confirmation(host):
    target = get_shutdown_target(host)
    if not target:
        return f"재부팅 허용 목록에 없는 서버입니다: {host}\n\n재부팅목록 으로 확인하세요."

    code = str(int(time.time() * 1000) % 9000 + 1000)
    reboot_confirmations[target["name"]] = {
        "code": code,
        "expires_at": time.time() + SHUTDOWN_CONFIRM_TTL,
    }
    return (
        f"{target['name']} 서버 재부팅 확인\n\n"
        f"정말 재부팅하려면 {SHUTDOWN_CONFIRM_TTL}초 안에 아래처럼 입력하세요.\n"
        f"확인재부팅 {target['name']} {code}"
    )

def confirm_reboot(host, code):
    target = get_shutdown_target(host)
    if not target:
        return f"재부팅 허용 목록에 없는 서버입니다: {host}"

    pending = reboot_confirmations.get(target["name"])
    if not pending:
        return f"{target['name']} 재부팅 확인 요청이 없습니다."
    if time.time() > pending["expires_at"]:
        reboot_confirmations.pop(target["name"], None)
        return f"{target['name']} 재부팅 확인 시간이 만료됐습니다."
    if code.strip() != pending["code"]:
        return f"{target['name']} 재부팅 확인 코드가 틀렸습니다."

    reboot_confirmations.pop(target["name"], None)
    try:
        result = execute_remote_reboot(target)
        return f"{target['name']} 재부팅 명령 전송 완료\n{result}"
    except Exception as e:
        return f"{target['name']} 재부팅 명령 실패: {e}"

def handle_shutdown_command(text):
    parts = text.strip().split()
    if not parts:
        return None

    if parts[0] in ["종료목록", "재부팅목록", "/shutdowns", "/reboots"]:
        return get_shutdown_list_message()

    if parts[0] in ["종료", "/shutdown"]:
        if len(parts) != 2:
            return "사용법: 종료 서버명"
        return request_shutdown_confirmation(parts[1])

    if parts[0] in ["재부팅", "/reboot"]:
        if len(parts) != 2:
            return "사용법: 재부팅 서버명"
        return request_reboot_confirmation(parts[1])

    if parts[0] in ["확인", "/confirm"]:
        if len(parts) != 3:
            return "사용법: 확인 서버명 코드"
        return confirm_shutdown(parts[1], parts[2])

    if parts[0] in ["확인재부팅", "/confirmreboot"]:
        if len(parts) != 3:
            return "사용법: 확인재부팅 서버명 코드"
        return confirm_reboot(parts[1], parts[2])

    return None

def fetch_devices_raw():
    token = get_access_token()
    if not token: return None

    url = f"https://api.tailscale.com/api/v2/tailnet/{TS_TAILNET}/devices"
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"API 에러: {resp.text}")
            return None
        return resp.json().get('devices', [])
    except Exception as e:
        print(f"API 요청 실패: {e}")
        return None

def get_device_name(dev):
    name = (dev.get("name") or "").split(".")[0]
    return name or dev.get("hostname") or "unknown"

def dedupe_devices(devices):
    by_key = {}

    def parse_last_seen(dev):
        last_seen = dev.get("lastSeen")
        if not last_seen:
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            return datetime.strptime(last_seen, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    for dev in devices:
        key = get_device_name(dev).lower()
        current = by_key.get(key)
        if not current or parse_last_seen(dev) >= parse_last_seen(current):
            by_key[key] = dev

    return sorted(by_key.values(), key=lambda item: get_device_name(item).lower())

def get_allowed_host_list():
    return sorted(SHUTDOWN_ALLOWED_HOSTS)

def get_numbered_server_list(title, action_word):
    hosts = get_allowed_host_list()
    lines = [f"[{title}]"]
    for index, host in enumerate(hosts, start=1):
        lines.append(f"{index}. {host}")
    lines.append("")
    lines.append("60초 안에 번호를 입력하세요. 예: 1")
    lines.append(f"직접 입력도 가능: {action_word} 서버명")
    return "\n".join(lines)

def start_number_selection(action):
    hosts = get_allowed_host_list()
    command_selection["action"] = action
    command_selection["hosts"] = hosts
    command_selection["expires_at"] = time.time() + SHUTDOWN_CONFIRM_TTL
    title = "종료할 서버 선택" if action == "shutdown" else "재부팅할 서버 선택"
    action_word = "종료" if action == "shutdown" else "재부팅"
    return get_numbered_server_list(title, action_word)

def handle_number_selection(text):
    stripped = text.strip()

    if stripped.isdigit() and len(stripped) == 4 and shutdown_confirmations:
        for host, pending in list(shutdown_confirmations.items()):
            if time.time() > pending["expires_at"]:
                shutdown_confirmations.pop(host, None)
                continue
            if stripped == pending["code"]:
                return confirm_shutdown(host, stripped)

    if not stripped.isdigit():
        return None

    session = command_selection
    if not session or time.time() > session.get("expires_at", 0):
        command_selection.clear()
        return None

    index = int(stripped)
    hosts = session.get("hosts", [])
    if index < 1 or index > len(hosts):
        return f"번호가 범위를 벗어났습니다. 1-{len(hosts)} 중에서 입력하세요."

    host = hosts[index - 1]
    action = session.get("action")
    command_selection.clear()

    if action == "shutdown":
        return request_shutdown_confirmation(host)
    if action == "reboot":
        return request_reboot_confirmation(host)
    return None

def handle_shutdown_command(text):
    parts = text.strip().split()
    if not parts:
        return None

    selected = handle_number_selection(text)
    if selected:
        return selected

    command = parts[0]

    if command in ["종료목록", "/shutdowns"]:
        return get_numbered_server_list("종료 가능 서버", "종료")

    if command in ["재부팅목록", "/reboots"]:
        return get_numbered_server_list("재부팅 가능 서버", "재부팅")

    if command in ["종료", "/shutdown"]:
        if len(parts) == 1:
            return start_number_selection("shutdown")
        if len(parts) == 2:
            return request_shutdown_confirmation(parts[1])
        return "사용법: 종료 또는 종료 서버명"

    if command in ["재부팅", "/reboot"]:
        if len(parts) == 1:
            return start_number_selection("reboot")
        if len(parts) == 2:
            return request_reboot_confirmation(parts[1])
        return "사용법: 재부팅 또는 재부팅 서버명"

    if command in ["확인", "/confirm"]:
        if len(parts) != 3:
            return "사용법: 확인 서버명 코드"
        return confirm_shutdown(parts[1], parts[2])

    if command in ["확인재부팅", "/confirmreboot"]:
        if len(parts) != 3:
            return "사용법: 확인재부팅 서버명 코드"
        return confirm_reboot(parts[1], parts[2])

    return None

def format_time_diff(diff):
    days = diff.days
    seconds = diff.seconds
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    
    if days > 0:
        return f"{days}일 {hours}시간 {minutes}분"
    elif hours > 0:
        return f"{hours}시간 {minutes}분"
    else:
        return f"{minutes}분"

def get_status_report():
    devices = fetch_devices_raw()
    if devices is not None:
        devices = dedupe_devices(devices)
    if devices is None: return "⚠️ API 조회 실패"

    lines = []
    now_utc = datetime.now(timezone.utc)

    for dev in devices:
        name = get_device_name(dev)

        last_seen_str = dev.get('lastSeen')
        if not last_seen_str: continue

        last_seen_utc = datetime.strptime(last_seen_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        diff = now_utc - last_seen_utc
        diff_min = int(diff.total_seconds() / 60)
        time_str = format_time_diff(diff)

        if diff_min > OFFLINE_THRESHOLD_MIN:
            lines.append(f"🔴 {name} : Offline ({time_str} 전)")
        else:
            lines.append(f"🟢 {name} : Online")
            
    return f"📊 **[현재 상태 조회]**\n\n" + "\n".join(lines)

def check_for_alerts(is_first_run=False):
    global device_status_history
    devices = fetch_devices_raw()
    if devices is not None:
        devices = dedupe_devices(devices)
    if devices is None: return

    now_utc = datetime.now(timezone.utc)
    alert_messages = []

    for dev in devices:
        name = get_device_name(dev)
        
        last_seen_str = dev.get('lastSeen')
        if not last_seen_str: continue

        last_seen_utc = datetime.strptime(last_seen_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        diff = now_utc - last_seen_utc
        diff_min = int(diff.total_seconds() / 60)
        time_str = format_time_diff(diff)
        
        is_currently_offline = diff_min > OFFLINE_THRESHOLD_MIN

        if name not in device_status_history:
            device_status_history[name] = is_currently_offline
            continue

        was_offline = device_status_history[name]

        if was_offline and not is_currently_offline:
            alert_messages.append(f"🟢 **{name}** 연결 복구됨! (Online)")
        elif not was_offline and is_currently_offline:
            alert_messages.append(f"🔴 **{name}** 연결 끊김! ({time_str} 째)")

        device_status_history[name] = is_currently_offline

    if INTERNAL_ALERTS_ENABLED and not is_first_run and alert_messages:
        full_msg = "🚨 **[상태 변경 알림]**\n\n" + "\n".join(alert_messages)
        send_telegram(full_msg)
        print(f"알림 전송함: {len(alert_messages)}건")

CMD_SHUTDOWN = "\uc885\ub8cc"
CMD_SHUTDOWN_LIST = "\uc885\ub8cc\ubaa9\ub85d"
CMD_REBOOT = "\uc7ac\ubd80\ud305"
CMD_REBOOT_LIST = "\uc7ac\ubd80\ud305\ubaa9\ub85d"
CMD_CONFIRM = "\ud655\uc778"
CMD_CONFIRM_REBOOT = "\ud655\uc778\uc7ac\ubd80\ud305"
CMD_WOL = "WOL"

def get_wol_host_list():
    return sorted(WOL_TARGETS)

def get_wol_list_message():
    hosts = get_wol_host_list()
    lines = ["[WOL 가능 서버]"]
    for index, host in enumerate(hosts, start=1):
        target = WOL_TARGETS[host]
        lines.append(f"{index}. {host} ({target['mac']})")
    lines.append("")
    lines.append("60초 안에 번호를 입력하세요. 예: 1")
    lines.append("직접 입력도 가능: WOL 서버명")
    return "\n".join(lines)

def start_wol_selection():
    command_selection["action"] = "wol"
    command_selection["hosts"] = get_wol_host_list()
    command_selection["expires_at"] = time.time() + SHUTDOWN_CONFIRM_TTL
    return get_wol_list_message()

def execute_wol(host):
    host = host.strip().lower()
    target = WOL_TARGETS.get(host)
    if not target:
        return f"WOL 대상 목록에 없는 서버입니다: {host}"

    mac = target["mac"]
    relay = target["relay"]
    relay_user = target["relay_user"]
    broadcast = target["broadcast"]
    port = int(target.get("port", 9))
    remote_script = f"""
import socket
mac = "{mac}".replace(":", "").replace("-", "")
packet = bytes.fromhex("ff" * 6 + mac * 16)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
sock.sendto(packet, ("{broadcast}", {port}))
print("sent")
"""
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=8",
        f"{relay_user}@{relay}",
        "python3 - <<'PY'\n" + remote_script + "\nPY",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        return f"{host} WOL 실패: {result.stderr.strip() or result.stdout.strip() or result.returncode}"
    return f"{host} WOL 패킷 전송 완료\nrelay: {relay}\nmac: {mac}"

def get_allowed_host_list():
    return sorted(SHUTDOWN_ALLOWED_HOSTS)

def get_numbered_server_list(title, action_word):
    hosts = get_allowed_host_list()
    lines = [f"[{title}]"]
    for index, host in enumerate(hosts, start=1):
        lines.append(f"{index}. {host}")
    lines.append("")
    lines.append("60\ucd08 \uc548\uc5d0 \ubc88\ud638\ub97c \uc785\ub825\ud558\uc138\uc694. \uc608: 1")
    lines.append(f"\uc9c1\uc811 \uc785\ub825\ub3c4 \uac00\ub2a5: {action_word} \uc11c\ubc84\uba85")
    return "\n".join(lines)

def start_number_selection(action):
    hosts = get_allowed_host_list()
    command_selection["action"] = action
    command_selection["hosts"] = hosts
    command_selection["expires_at"] = time.time() + SHUTDOWN_CONFIRM_TTL
    title = "\uc885\ub8cc\ud560 \uc11c\ubc84 \uc120\ud0dd" if action == "shutdown" else "\uc7ac\ubd80\ud305\ud560 \uc11c\ubc84 \uc120\ud0dd"
    action_word = CMD_SHUTDOWN if action == "shutdown" else CMD_REBOOT
    return get_numbered_server_list(title, action_word)

def handle_number_selection(text):
    stripped = text.strip()
    if not stripped.isdigit():
        return None

    session = command_selection
    if not session or time.time() > session.get("expires_at", 0):
        command_selection.clear()
        return None

    index = int(stripped)
    hosts = session.get("hosts", [])
    if index < 1 or index > len(hosts):
        return f"\ubc88\ud638\uac00 \ubc94\uc704\ub97c \ubc97\uc5b4\ub0ac\uc2b5\ub2c8\ub2e4. 1-{len(hosts)} \uc911\uc5d0\uc11c \uc785\ub825\ud558\uc138\uc694."

    host = hosts[index - 1]
    action = session.get("action")
    command_selection.clear()

    if action == "shutdown":
        return request_shutdown_confirmation(host)
    if action == "reboot":
        return request_reboot_confirmation(host)
    if action == "wol":
        return execute_wol(host)
    return None

def handle_shutdown_command(text):
    parts = text.strip().split()
    if not parts:
        return None

    selected = handle_number_selection(text)
    if selected:
        return selected

    command = parts[0]

    if command in [CMD_SHUTDOWN_LIST, "/shutdowns"]:
        return get_numbered_server_list("\uc885\ub8cc \uac00\ub2a5 \uc11c\ubc84", CMD_SHUTDOWN)

    if command in [CMD_REBOOT_LIST, "/reboots"]:
        return get_numbered_server_list("\uc7ac\ubd80\ud305 \uac00\ub2a5 \uc11c\ubc84", CMD_REBOOT)

    if command.upper() == CMD_WOL:
        if len(parts) == 1:
            return start_wol_selection()
        if len(parts) == 2:
            return execute_wol(parts[1])
        return "사용법: WOL 또는 WOL 서버명"

    if command in [CMD_SHUTDOWN, "/shutdown"]:
        if len(parts) == 1:
            return start_number_selection("shutdown")
        if len(parts) == 2:
            return request_shutdown_confirmation(parts[1])
        return "\uc0ac\uc6a9\ubc95: \uc885\ub8cc \ub610\ub294 \uc885\ub8cc \uc11c\ubc84\uba85"

    if command in [CMD_REBOOT, "/reboot"]:
        if len(parts) == 1:
            return start_number_selection("reboot")
        if len(parts) == 2:
            return request_reboot_confirmation(parts[1])
        return "\uc0ac\uc6a9\ubc95: \uc7ac\ubd80\ud305 \ub610\ub294 \uc7ac\ubd80\ud305 \uc11c\ubc84\uba85"

    if command in [CMD_CONFIRM, "/confirm"]:
        if len(parts) != 3:
            return "\uc0ac\uc6a9\ubc95: \ud655\uc778 \uc11c\ubc84\uba85 \ucf54\ub4dc"
        return confirm_shutdown(parts[1], parts[2])

    if command in [CMD_CONFIRM_REBOOT, "/confirmreboot"]:
        if len(parts) != 3:
            return "\uc0ac\uc6a9\ubc95: \ud655\uc778\uc7ac\ubd80\ud305 \uc11c\ubc84\uba85 \ucf54\ub4dc"
        return confirm_reboot(parts[1], parts[2])

    return None

def load_wol_db():
    data = {}
    if os.path.exists(WOL_DB_PATH):
        try:
            with open(WOL_DB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"WOL DB load failed: {e}")
    merged = dict(WOL_TARGETS)
    merged.update(data)
    return merged

def save_wol_db(data):
    os.makedirs(os.path.dirname(WOL_DB_PATH), exist_ok=True)
    with open(WOL_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)

def run_ssh_text(target, remote_cmd, timeout=15):
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=8",
        f"{target['user']}@{target['host']}",
        remote_cmd,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or result.returncode)
    return result.stdout

def get_node_ssh_target(node):
    return make_resource_target_from_node(node)

def collect_lan_info(target):
    script = r"""
import ipaddress
import json
import os
import subprocess

skip = ("lo", "tailscale", "docker", "veth", "br-", "tap", "fw")

def include_iface(name):
    return not any(name.startswith(prefix) for prefix in skip)

addrs = []
for line in subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True).stdout.splitlines():
    parts = line.split()
    if len(parts) < 4:
        continue
    iface = parts[1]
    cidr = parts[3]
    if not include_iface(iface):
        continue
    mac_path = f"/sys/class/net/{iface}/address"
    mac = ""
    if os.path.exists(mac_path):
        mac = open(mac_path, encoding="utf-8").read().strip().lower()
    try:
        iface_obj = ipaddress.ip_interface(cidr)
    except Exception:
        continue
    addrs.append({
        "iface": iface,
        "ip": str(iface_obj.ip),
        "network": str(iface_obj.network),
        "broadcast": str(iface_obj.network.broadcast_address),
        "mac": mac,
    })

neighbors = []
for line in subprocess.run(["ip", "neigh", "show"], capture_output=True, text=True).stdout.splitlines():
    parts = line.split()
    if "lladdr" not in parts or "dev" not in parts:
        continue
    try:
        ip = parts[0]
        iface = parts[parts.index("dev") + 1]
        mac = parts[parts.index("lladdr") + 1].lower()
    except Exception:
        continue
    if include_iface(iface):
        neighbors.append({"ip": ip, "iface": iface, "mac": mac})

print(json.dumps({"addrs": addrs, "neighbors": neighbors}))
"""
    if target.get("local"):
        result = subprocess.run(["python3", "-c", script], capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or result.returncode)
        return json.loads(result.stdout)
    return json.loads(run_ssh_text(target, "python3 - <<'PY'\n" + script + "\nPY", timeout=20))

def get_online_linux_nodes():
    try:
        return [n for n in get_tailscale_nodes() if n.get("Online") and n.get("OS") == "linux"]
    except Exception as e:
        print(f"WOL node scan failed: {e}")
        return []

def discover_wol_targets():
    nodes = get_online_linux_nodes()
    relay_infos = {}
    host_infos = {}

    for node in nodes:
        name = get_tailscale_node_name(node)
        try:
            target = get_node_ssh_target(node)
            info = collect_lan_info(target)
            host_infos[name] = info
            if info.get("addrs"):
                relay_infos[name] = {"target": target, "info": info}
        except Exception as e:
            print(f"WOL scan skip {name}: {e}")

    learned = load_wol_db()
    static_names = set(WOL_TARGETS)

    for name, info in host_infos.items():
        if name == "bot":
            continue
        current = learned.get(name, {})
        mac = current.get("mac")
        networks = []
        for addr in info.get("addrs", []):
            networks.append(addr["network"])
            if addr.get("mac") and addr["mac"] != "00:00:00:00:00:00":
                mac = addr["mac"]

        if not mac:
            continue

        current.update({"mac": mac, "networks": networks, "port": int(current.get("port", 9))})
        learned[name] = current

    for name, current in list(learned.items()):
        target_networks = current.get("networks", [])
        mac = current.get("mac")
        if not mac:
            continue

        best = None
        for relay_name, relay in relay_infos.items():
            if relay_name == name:
                continue
            for relay_addr in relay["info"].get("addrs", []):
                relay_network = ipaddress.ip_network(relay_addr["network"], strict=False)
                for target_net in target_networks:
                    try:
                        if relay_network.overlaps(ipaddress.ip_network(target_net, strict=False)):
                            best = (relay_name, relay_addr)
                            break
                    except Exception:
                        continue
                if best:
                    break
            if best:
                break

        # ARP/neighbor fallback: useful when the target is currently offline but relay remembers it.
        if not best:
            for relay_name, relay in relay_infos.items():
                if relay_name == name:
                    continue
                for neighbor in relay["info"].get("neighbors", []):
                    if neighbor.get("mac") == mac:
                        for relay_addr in relay["info"].get("addrs", []):
                            if relay_addr["iface"] == neighbor.get("iface"):
                                best = (relay_name, relay_addr)
                                break
                    if best:
                        break
                if best:
                    break

        if best:
            relay_name, relay_addr = best
            current.update({
                "relay": relay_name,
                "relay_user": RESOURCE_SSH_USERS.get(relay_name, "ubuntu"),
                "broadcast": relay_addr["broadcast"],
                "port": int(current.get("port", 9)),
                "auto": True,
            })
            learned[name] = current

    save_wol_db(learned)
    usable = [name for name, target in learned.items() if target.get("mac") and target.get("relay") and target.get("broadcast")]
    return learned, usable, static_names

def get_wol_targets():
    return load_wol_db()

def get_wol_host_list():
    targets = get_wol_targets()
    return sorted([name for name, target in targets.items() if target.get("mac") and target.get("relay") and target.get("broadcast")])

def get_wol_list_message():
    hosts = get_wol_host_list()
    if not hosts:
        return "WOL 대상이 없습니다. 먼저 WOL스캔을 실행하세요."
    targets = get_wol_targets()
    lines = ["[WOL 가능 서버]"]
    for index, host in enumerate(hosts, start=1):
        target = targets[host]
        lines.append(f"{index}. {host} ({target['mac']} via {target['relay']})")
    lines.append("")
    lines.append("60초 안에 번호를 입력하세요. 예: 1")
    lines.append("직접 입력도 가능: WOL 서버명")
    return "\n".join(lines)

def execute_wol(host):
    host = host.strip().lower()
    targets = get_wol_targets()
    target = targets.get(host)
    if not target or not target.get("mac") or not target.get("relay"):
        discover_wol_targets()
        targets = get_wol_targets()
        target = targets.get(host)
    if not target or not target.get("mac") or not target.get("relay") or not target.get("broadcast"):
        return f"WOL 대상 정보를 찾지 못했습니다: {host}\nWOL스캔 후 다시 시도하세요."

    mac = target["mac"]
    relay = target["relay"]
    relay_user = target.get("relay_user") or RESOURCE_SSH_USERS.get(relay, "ubuntu")
    broadcast = target["broadcast"]
    port = int(target.get("port", 9))
    remote_script = f"""
import socket
mac = "{mac}".replace(":", "").replace("-", "")
packet = bytes.fromhex("ff" * 6 + mac * 16)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
sock.sendto(packet, ("{broadcast}", {port}))
print("sent")
"""
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=8",
        f"{relay_user}@{relay}",
        "python3 - <<'PY'\n" + remote_script + "\nPY",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        return f"{host} WOL 실패: {result.stderr.strip() or result.stdout.strip() or result.returncode}"
    return f"{host} WOL 패킷 전송 완료\nrelay: {relay}\nbroadcast: {broadcast}\nmac: {mac}"

def handle_wol_scan_command():
    learned, usable, _ = discover_wol_targets()
    lines = ["[WOL 스캔 완료]"]
    if usable:
        for name in sorted(usable):
            t = learned[name]
            lines.append(f"- {name}: {t.get('mac')} via {t.get('relay')} -> {t.get('broadcast')}")
    else:
        lines.append("사용 가능한 WOL 대상이 없습니다.")
    lines.append("")
    lines.append(f"저장 위치: {WOL_DB_PATH}")
    return "\n".join(lines)

def start_wol_selection():
    command_selection["action"] = "wol"
    command_selection["hosts"] = get_wol_host_list()
    command_selection["expires_at"] = time.time() + SHUTDOWN_CONFIRM_TTL
    return get_wol_list_message()

def handle_shutdown_command(text):
    parts = text.strip().split()
    if not parts:
        return None

    selected = handle_number_selection(text)
    if selected:
        return selected

    command = parts[0]

    if command in [CMD_SHUTDOWN_LIST, "/shutdowns"]:
        return get_numbered_server_list("\uc885\ub8cc \uac00\ub2a5 \uc11c\ubc84", CMD_SHUTDOWN)

    if command in [CMD_REBOOT_LIST, "/reboots"]:
        return get_numbered_server_list("\uc7ac\ubd80\ud305 \uac00\ub2a5 \uc11c\ubc84", CMD_REBOOT)

    if command.upper() == CMD_WOL:
        if len(parts) == 1:
            return start_wol_selection()
        if len(parts) == 2:
            return execute_wol(parts[1])
        return "사용법: WOL 또는 WOL 서버명"

    if command in ["WOL스캔", "wol스캔", "/wolscan"]:
        return handle_wol_scan_command()

    if command in [CMD_SHUTDOWN, "/shutdown"]:
        if len(parts) == 1:
            return start_number_selection("shutdown")
        if len(parts) == 2:
            return request_shutdown_confirmation(parts[1])
        return "\uc0ac\uc6a9\ubc95: \uc885\ub8cc \ub610\ub294 \uc885\ub8cc \uc11c\ubc84\uba85"

    if command in [CMD_REBOOT, "/reboot"]:
        if len(parts) == 1:
            return start_number_selection("reboot")
        if len(parts) == 2:
            return request_reboot_confirmation(parts[1])
        return "\uc0ac\uc6a9\ubc95: \uc7ac\ubd80\ud305 \ub610\ub294 \uc7ac\ubd80\ud305 \uc11c\ubc84\uba85"

    if command in [CMD_CONFIRM, "/confirm"]:
        if len(parts) != 3:
            return "\uc0ac\uc6a9\ubc95: \ud655\uc778 \uc11c\ubc84\uba85 \ucf54\ub4dc"
        return confirm_shutdown(parts[1], parts[2])

    if command in [CMD_CONFIRM_REBOOT, "/confirmreboot"]:
        if len(parts) != 3:
            return "\uc0ac\uc6a9\ubc95: \ud655\uc778\uc7ac\ubd80\ud305 \uc11c\ubc84\uba85 \ucf54\ub4dc"
        return confirm_reboot(parts[1], parts[2])

    return None

def load_manage_db():
    data = {}
    if os.path.exists(MANAGE_DB_PATH):
        try:
            with open(MANAGE_DB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"manage DB load failed: {e}")
    data = {str(host).strip().lower(): value for host, value in data.items() if str(host).strip()}
    for host in SHUTDOWN_ALLOWED_HOSTS:
        data.setdefault(host, {"user": RESOURCE_SSH_USERS.get(host, "ubuntu"), "source": "static"})
    return data

def save_manage_db(data):
    os.makedirs(os.path.dirname(MANAGE_DB_PATH), exist_ok=True)
    with open(MANAGE_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)

def discover_manage_targets():
    data = load_manage_db()
    for node in get_online_linux_nodes():
        name = get_tailscale_node_name(node)
        if name == "bot":
            continue
        target = make_resource_target_from_node(node)
        try:
            run_ssh_text(target, "true", timeout=10)
        except Exception as e:
            print(f"manage scan skip {name}: {e}")
            continue
        data[name] = {
            "user": target.get("user", "ubuntu"),
            "source": "scan",
            "last_seen": datetime.now(KST).isoformat(timespec="seconds"),
        }
    save_manage_db(data)
    return data

def get_shutdown_target(host):
    host = host.strip().lower()
    data = load_manage_db()
    item = data.get(host)
    if not item:
        return None
    return {"name": host, "host": host, "user": item.get("user") or RESOURCE_SSH_USERS.get(host, "ubuntu")}

def get_allowed_host_list():
    return sorted(load_manage_db())

def handle_wol_scan_command():
    manage = discover_manage_targets()
    learned, usable, _ = discover_wol_targets()
    lines = ["[스캔 완료]"]

    lines.append("")
    lines.append("[종료/재부팅 가능]")
    for name in sorted(manage):
        user = manage[name].get("user", "ubuntu")
        source = manage[name].get("source", "")
        lines.append(f"- {name} ({user}, {source})")

    lines.append("")
    lines.append("[WOL 가능]")
    if usable:
        for name in sorted(usable):
            t = learned[name]
            lines.append(f"- {name}: {t.get('mac')} via {t.get('relay')} -> {t.get('broadcast')}")
    else:
        lines.append("- 없음")

    known_macs = {str(t.get("mac", "")).lower() for t in learned.values() if t.get("mac")}
    unknown = []
    for relay_name in sorted(manage):
        try:
            relay = collect_lan_info(get_shutdown_target(relay_name) or {"host": relay_name, "user": RESOURCE_SSH_USERS.get(relay_name, "ubuntu")})
            for neighbor in relay.get("neighbors", []):
                mac = neighbor.get("mac", "").lower()
                if mac and mac not in known_macs:
                    unknown.append((relay_name, neighbor.get("ip"), mac))
        except Exception as e:
            print(f"unknown WOL candidates skip {relay_name}: {e}")

    if unknown:
        lines.append("")
        lines.append("[미식별 LAN MAC 후보]")
        unknown = sorted(unknown, key=lambda item: (":" in str(item[1]), item[0], str(item[1])))
        for relay_name, ip, mac in unknown[:25]:
            lines.append(f"- {ip} {mac} via {relay_name}")

    lines.append("")
    lines.append(f"WOL 저장: {WOL_DB_PATH}")
    lines.append(f"관리 저장: {MANAGE_DB_PATH}")
    return "\n".join(lines)

MENU_MOONCEN = "문센 서비스 요약"
MENU_RESOURCE = "서버 성능 요약"
MENU_SHUTDOWN = "서버 종료"
MENU_WOL = "WOL"

def telegram_main_menu_markup():
    return {
        "keyboard": [
            [{"text": MENU_MOONCEN}, {"text": MENU_RESOURCE}],
            [{"text": MENU_SHUTDOWN}, {"text": MENU_WOL}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }

def get_main_menu_message():
    return "[MoonCen 메뉴]\n\n원하는 항목을 선택하세요."

def ok_mark(value):
    return "OK" if value else "DOWN"

def fetch_json_url(url, timeout=5):
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()

def get_mooncen_ops_api_summary():
    last_error = ""
    for url in MOONCEN_OPS_STATUS_URLS:
        try:
            data = fetch_json_url(url, timeout=6)
        except Exception as e:
            last_error = f"{url}: {e}"
            continue

        lines = ["[문센 서비스 요약]", f"source: {url}", ""]
        for item in data.get("checks", []):
            label = item.get("label") or item.get("key") or "UNKNOWN"
            lines.append(f"- {label}: {ok_mark(item.get('ok'))} ({item.get('detail') or '-'})")

        crawler = data.get("crawler") or {}
        progress = crawler.get("progress") or {}
        if progress:
            lines.append("")
            lines.append(
                "crawler: "
                f"{progress.get('status') or 'unknown'} "
                f"{progress.get('completed') or 0}/{progress.get('total') or 0}, "
                f"failed {progress.get('failed') or 0}, pending {progress.get('pending') or 0}"
            )

        servers = data.get("servers") or []
        if servers:
            lines.append("")
            lines.append("[servers]")
            for row in servers[:8]:
                lines.append(
                    f"- {row.get('name') or '-'}({row.get('role') or '-'}) "
                    f"{ok_mark(row.get('reachable'))} "
                    f"CPU {row.get('cpu_percent') if row.get('cpu_percent') is not None else '-'} "
                    f"MEM {row.get('memory_percent') if row.get('memory_percent') is not None else '-'}"
                )
        return "\n".join(lines)

    return f"[문센 서비스 요약]\n\nOps Console API 응답 없음.\n{last_error}\n\n" + get_mooncen_prometheus_service_summary()

def instant_vector(query):
    try:
        data = prometheus_query(query)
        return data or []
    except Exception:
        return []

def first_value(query):
    rows = instant_vector(query)
    if not rows:
        return None
    try:
        return float(rows[0].get("value", [None, None])[1])
    except Exception:
        return None

def get_mooncen_prometheus_service_summary():
    checks = [
        ("cloud db", 'node_systemd_unit_state{node="cloud",name="postgresql.service",state="active"}'),
        ("cloud frontend", 'node_systemd_unit_state{node="cloud",name="mooncen-frontend.service",state="active"}'),
        ("cloud backend", 'node_systemd_unit_state{node="cloud",name="mooncen-api.service",state="active"}'),
        ("cloud cloudflare", 'node_systemd_unit_state{node="cloud",name="cloudflared.service",state="active"}'),
        ("gen1db db", 'node_systemd_unit_state{node="gen1db",name="postgresql.service",state="active"}'),
        ("gen1web backend", 'node_systemd_unit_state{node="gen1web",name="mooncen-api.service",state="active"}'),
        ("gen1web frontend", 'node_systemd_unit_state{node="gen1web",name="nginx.service",state="active"}'),
        ("gen1crawler crawler", 'node_systemd_unit_state{node="gen1crawler",name="mooncen-crawler.service",state="active"} or node_systemd_unit_state{node="gen1crawler",name="crawler.service",state="active"}'),
        ("wtr-linux ollama", 'node_systemd_unit_state{node="wtr-linux",name="ollama.service",state="active"}'),
    ]
    lines = ["[문센 서비스 요약]", ""]
    for label, query in checks:
        value = first_value(query)
        lines.append(f"- {label}: {ok_mark(value == 1)}")
    return "\n".join(lines)

def get_server_performance_summary():
    rows = instant_vector("mooncen_node_summary_info")
    if not rows:
        return "[서버 성능 요약]\n\nPrometheus 요약 메트릭이 없습니다."
    lines = ["[서버 성능 요약]", ""]
    for row in sorted(rows, key=lambda item: item.get("metric", {}).get("node", "")):
        m = row.get("metric", {})
        lines.append(
            f"{m.get('node', '-')}: {m.get('up', '-')} "
            f"CPU {m.get('cpu', '-')} MEM {m.get('mem', '-')} "
            f"DISK {m.get('disk', '-')} TEMP {m.get('temp', '-')} "
            f"UP {m.get('uptime', '-')} ({m.get('role', '-')})"
        )
    return "\n".join(lines)

def handle_menu_command(text):
    command = text.strip()
    if command in ("/start", "/menu", "메뉴", "menu"):
        return get_main_menu_message()
    if command == MENU_MOONCEN:
        return get_mooncen_ops_api_summary()
    if command == MENU_RESOURCE:
        return get_server_performance_summary()
    if command == MENU_SHUTDOWN:
        return start_number_selection("shutdown")
    if command == MENU_WOL:
        return start_wol_selection()
    return None

def main():
    start_prometheus_metrics_server()
    print("🤖 봇 초기화 중...")
    
    # 1. 내부 상태 학습 (알림 X)
    check_for_alerts(is_first_run=True)
    print(f"✅ 초기 상태 로드 완료 (감시 대상: {len(device_status_history)}개 기기)")
    
    # 2. [수정됨] 현재 상태 리포트 생성 및 전송
    print("📊 초기 리포트 전송 중...")
    initial_report = get_status_report()
    welcome_msg = f"🚀 **Tailscale 모니터링 봇이 시작되었습니다.**\n\n{initial_report}"
    if INTERNAL_ALERTS_ENABLED:
        send_telegram(welcome_msg)
    else:
        send_telegram(get_main_menu_message(), telegram_main_menu_markup())
    
    last_update_id = 0
    last_auto_check = time.time()
    last_resource_check = time.time()

    while True:
        try:
            if not TELEGRAM_ENABLED:
                time.sleep(1)
                if INTERNAL_ALERTS_ENABLED and time.time() - last_auto_check > AUTO_CHECK_INTERVAL:
                    print(f"???먮룞 ?먭? ({datetime.now().strftime('%H:%M')})")
                    check_for_alerts(is_first_run=False)
                    last_auto_check = time.time()

                if time.time() - last_resource_check > RESOURCE_CHECK_INTERVAL:
                    print(f"?뱢 由ъ냼???먭? ({datetime.now(KST).strftime('%H:%M')})")
                    check_resource_alerts()
                    last_resource_check = time.time()
                continue

            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/getUpdates"
            params = {"offset": last_update_id + 1, "timeout": 30}
            resp = requests.get(url, params=params, timeout=40)
            
            if resp.status_code == 200:
                updates = resp.json().get("result", [])
                for update in updates:
                    last_update_id = update["update_id"]
                    
                    if "message" in update and str(update["message"]["chat"]["id"]) == TG_CHAT_ID:
                        text = update["message"].get("text", "")
                        menu_response = handle_menu_command(text)
                        if menu_response:
                            send_telegram(menu_response, telegram_main_menu_markup())
                            continue

                        shutdown_response = handle_shutdown_command(text)
                        if shutdown_response:
                            send_telegram(shutdown_response, telegram_main_menu_markup())
                            continue
                        
                        if text in ["상태", "점검", "/status"]:
                            send_telegram("🔍 상태 확인 중...")
                            report = get_status_report()
                            send_telegram(report)
                        elif text in ["리소스", "자원", "성능", "/resources", "/resource"]:
                            send_telegram("🔍 리소스 상태 확인 중...")
                            report = get_resource_report()
                            send_telegram(report)

            if INTERNAL_ALERTS_ENABLED and time.time() - last_auto_check > AUTO_CHECK_INTERVAL:
                print(f"⏰ 자동 점검 ({datetime.now().strftime('%H:%M')})")
                check_for_alerts(is_first_run=False)
                last_auto_check = time.time()

            if time.time() - last_resource_check > RESOURCE_CHECK_INTERVAL:
                print(f"📈 리소스 점검 ({datetime.now(KST).strftime('%H:%M')})")
                check_resource_alerts()
                last_resource_check = time.time()

            time.sleep(0.5)

        except Exception as e:
            print(f"에러 (재시도): {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()
