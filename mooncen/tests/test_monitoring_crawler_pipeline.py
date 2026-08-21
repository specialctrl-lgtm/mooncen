from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
MONITORING = ROOT / "deploy" / "monitoring"
ALERTS = MONITORING / "grafana" / "provisioning" / "alerting" / "rules-mooncen-app.yml"
DASHBOARD = (
    MONITORING
    / "grafana"
    / "provisioning"
    / "dashboards"
    / "json"
    / "mooncen-node-summary.json"
)


def _alert_rules() -> dict[str, dict]:
    document = yaml.safe_load(ALERTS.read_text(encoding="utf-8"))
    return {
        rule["uid"]: rule
        for group in document["groups"]
        for rule in group["rules"]
    }


def _deleted_alert_rule_uids() -> set[str]:
    document = yaml.safe_load(ALERTS.read_text(encoding="utf-8"))
    return {rule["uid"] for rule in document.get("deleteRules", [])}


def _prometheus_expression(rule: dict) -> str:
    query = next(item for item in rule["data"] if item["refId"] == "A")
    return query["model"]["expr"]


def test_monitoring_provisioning_yaml_and_dashboard_json_parse() -> None:
    yaml_paths = sorted((MONITORING / "grafana" / "provisioning").rglob("*.yml"))
    yaml_paths.append(MONITORING / "prometheus" / "prometheus.yml")
    for path in yaml_paths:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(document, dict), path

    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    panel_ids = [panel["id"] for panel in dashboard["panels"]]
    assert len(panel_ids) == len(set(panel_ids))
    assert dashboard["uid"] == "mooncen-node-summary"
    assert dashboard["version"] >= 2


def test_crawler_alert_fails_closed_for_every_terminal_health_signal() -> None:
    rule = _alert_rules()["mooncen_gen1crawler_crawler_unhealthy"]
    expression = _prometheus_expression(rule)

    required_evidence = {
        "mooncen_crawler_cycle_state_valid",
        "mooncen_crawler_cycle_outcome",
        "failed|partial_success|zero_provider",
        "mooncen_crawler_cycle_partial_success",
        "mooncen_crawler_cycle_zero_provider",
        "mooncen_crawler_cycle_providers_requested",
        "mooncen_crawler_cycle_providers_failed",
        "mooncen_crawler_cycle_last_completion_timestamp_seconds",
        "mooncen-crawler-once.service",
        "129600",
    }
    for evidence in required_evidence:
        assert evidence in expression
    assert "absent(mooncen_crawler_cycle_state_valid" in expression
    assert "or vector(0)" in expression
    assert "or vector(1)" in expression
    assert rule["noDataState"] == "Alerting"
    assert "completed only partially" in rule["annotations"]["description"]
    assert '{node="gen1crawler"' in expression
    assert '{node="cloud"' not in expression
    assert rule["labels"]["node"] == "gen1crawler"


def test_scheduler_alert_covers_collection_and_pinned_promotion() -> None:
    rule = _alert_rules()["mooncen_gen1crawler_crawler_down"]
    expression = _prometheus_expression(rule)

    for timer in ("mooncen-crawler.timer", "mooncen-staging-apply.timer"):
        assert expression.count(timer) >= 4
    assert "mooncen-staging-apply.service" in expression
    assert "mooncen_systemd_unit_active" in expression
    assert "mooncen_systemd_unit_enabled" in expression
    assert "mooncen_systemd_unit_result_failed" in expression
    assert "absent(" in expression
    assert rule["noDataState"] == "Alerting"
    assert "direct-write" not in json.dumps(rule)
    assert "isolated-staging" in rule["annotations"]["description"]
    assert "pinned staging-to-primary promotion" in rule["annotations"]["description"]
    assert '{node="gen1crawler"' in expression
    assert '{node="cloud"' not in expression
    assert rule["labels"]["node"] == "gen1crawler"


def test_node_summary_exposes_bounded_crawler_cycle_and_pipeline_views() -> None:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    panels = {panel["title"]: panel for panel in dashboard["panels"]}

    for title in (
        "Crawler Outcome",
        "Crawler Freshness",
        "Crawler Providers",
        "Crawler Pipeline",
    ):
        assert title in panels
    crawler_expressions = "\n".join(
        target["expr"]
        for panel in panels.values()
        for target in panel.get("targets", [])
        if panel["title"].startswith("Crawler")
    )
    for metric in (
        "mooncen_crawler_cycle_outcome",
        "mooncen_crawler_cycle_last_completion_timestamp_seconds",
        "mooncen_crawler_last_success_timestamp_seconds",
        "mooncen_crawler_cycle_providers_requested",
        "mooncen_crawler_cycle_providers_completed",
        "mooncen_crawler_cycle_providers_failed",
        "mooncen_crawler_cycle_state_valid",
        "mooncen_crawler_cycle_skipped_lock_contention",
        "mooncen-staging-apply.timer",
    ):
        assert metric in crawler_expressions
    assert "provider=~" not in crawler_expressions
    assert "batch" not in crawler_expressions
    assert '{node="gen1crawler"' in crawler_expressions
    assert '{node="cloud"' not in crawler_expressions


def test_crawler_metric_labels_are_fixed_and_prometheus_scrapes_runtime_host() -> None:
    metrics = (MONITORING / "mooncen_node_metrics.sh").read_text(encoding="utf-8")
    assert "for candidate in success partial_success failed zero_provider running unknown" in metrics
    assert 'mooncen_crawler_cycle_outcome{outcome="%s"}' in metrics
    assert 'mooncen_crawler_cycle_outcome{provider=' not in metrics
    assert 'mooncen_crawler_cycle_outcome{batch=' not in metrics
    assert "primary|standby|replica|backup|crawler|crawler-control|crawler-worker" in metrics
    assert (
        "for candidate in primary standby replica backup crawler crawler-control "
        "crawler-worker unknown" in metrics
    )

    prometheus = yaml.safe_load(
        (MONITORING / "prometheus" / "prometheus.yml").read_text(encoding="utf-8")
    )
    node_job = next(job for job in prometheus["scrape_configs"] if job["job_name"] == "node_exporter")
    cloud = next(
        target
        for target in node_job["static_configs"]
        if "cloud:9100" in target["targets"]
    )
    assert cloud["labels"]["node"] == "cloud"
    assert cloud["labels"]["alerting"] == "enabled"
    gen1crawler = next(
        target
        for target in node_job["static_configs"]
        if "gen1crawler:9100" in target["targets"]
    )
    assert gen1crawler["labels"] == {
        "node": "gen1crawler",
        "role": "crawler",
        "worker_key": "gen1crawler",
        "rollout_order": "2",
        "distributed_desired": "pending_disabled",
        "worker_installation": "blocked_not_ready",
        "alerting": "enabled",
    }
    gen1db = next(
        target
        for target in node_job["static_configs"]
        if "gen1db:9100" in target["targets"]
    )
    assert gen1db["labels"] == {
        "node": "gen1db",
        "role": "crawler-control",
        "alerting": "pending",
    }
    wtr = next(
        target
        for target in node_job["static_configs"]
        if "wtr-linux:9100" in target["targets"]
    )
    assert wtr["labels"] == {
        "node": "wtr-linux",
        "role": "crawler-worker",
        "worker_key": "wtr-linux",
        "rollout_order": "1",
        "distributed_desired": "pending_disabled",
        "worker_installation": "blocked_not_ready",
        "alerting": "pending",
    }
    topology = json.loads(
        (ROOT / "config" / "production_topology.json").read_text(encoding="utf-8")
    )
    assert topology["crawlerMode"] == "legacy"


def test_crawler_metrics_and_units_are_emitted_only_for_crawler_role() -> None:
    metrics = (MONITORING / "mooncen_node_metrics.sh").read_text(encoding="utf-8")
    assert (
        'if [ "$role" = "crawler" ]; then\n'
        '  cycle_state_values="$(crawler_cycle_state_values)"'
    ) in metrics
    assert (
        '  if [ "$role" = "crawler" ]; then\n'
        "    printf '# HELP mooncen_crawler_last_success_timestamp_seconds"
    ) in metrics
    assert (
        '  if [ "$role" = "crawler" ]; then\n'
        "    for unit in \\\n"
        "      mooncen-crawler.service"
    ) in metrics
    assert "primary|standby|replica|backup|crawler|crawler-control|crawler-worker" in metrics
    assert (
        "for candidate in primary standby replica backup crawler crawler-control "
        "crawler-worker unknown" in metrics
    )
    assert (
        '  if [ "$role" = "crawler-control" ]; then\n'
        "    for unit in \\\n"
        "      mooncen-crawler-control-scheduler.service"
    ) in metrics
    worker_block = metrics.split(
        '  if [ "$role" = "crawler-worker" ]; then',
        1,
    )[1].split("  fi", 1)[0]
    for unit in (
        "mooncen-crawler-pull-worker.service",
        "mooncen-crawler-release-agent.service",
        "mooncen-crawler-release-agent.timer",
        "mooncen-crawler-release-reporter.service",
        "mooncen-crawler-release-reporter.timer",
    ):
        assert unit in worker_block
    assert "mooncen-crawler.service" not in worker_block
    assert "mooncen-staging-apply" not in worker_block

    common_units = metrics.split(
        "  for unit in \\\n    postgresql.service",
        1,
    )[1].split("  done", 1)[0]
    assert "mooncen-crawler" not in common_units
    assert "mooncen-staging-apply" not in common_units


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_runtime_role_gates_crawler_series(tmp_path: Path) -> None:
    script = MONITORING / "mooncen_node_metrics.sh"
    app_dir = tmp_path / "app"
    textfile_dir = tmp_path / "metrics"
    role_file = tmp_path / "node-role"
    app_dir.mkdir()
    role_file.write_text("primary\n", encoding="utf-8")

    env = {
        **os.environ,
        "APP_DIR": str(app_dir),
        "TEXTFILE_DIR": str(textfile_dir),
        "ROLE_FILE": str(role_file),
    }
    completed = subprocess.run(
        [shutil.which("bash"), str(script)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    output = (textfile_dir / "mooncen.prom").read_text(encoding="utf-8")
    assert "mooncen_monitoring_collector_timestamp_seconds" in output
    assert "mooncen_deploy_timestamp_seconds" in output
    assert 'mooncen_systemd_unit_active{unit="mooncen-api.service"}' in output
    assert "mooncen_crawler_" not in output
    assert 'unit="mooncen-crawler' not in output
    assert 'unit="mooncen-staging-apply' not in output

    role_file.write_text("crawler\n", encoding="utf-8")
    completed = subprocess.run(
        [shutil.which("bash"), str(script)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    output = (textfile_dir / "mooncen.prom").read_text(encoding="utf-8")
    assert "mooncen_crawler_cycle_state_valid" in output
    assert 'mooncen_systemd_unit_active{unit="mooncen-crawler.timer"}' in output
    assert 'mooncen_systemd_unit_active{unit="mooncen-staging-apply.timer"}' in output

    role_file.write_text("crawler-control\n", encoding="utf-8")
    completed = subprocess.run(
        [shutil.which("bash"), str(script)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    output = (textfile_dir / "mooncen.prom").read_text(encoding="utf-8")
    assert "mooncen_crawler_cycle_state_valid" not in output
    assert 'mooncen_node_role{role="crawler-control"} 1' in output
    assert (
        'mooncen_systemd_unit_active{unit="mooncen-crawler-control-scheduler.service"}'
        in output
    )
    assert (
        'mooncen_systemd_unit_active{unit="mooncen-crawler-control-metrics.timer"}'
        in output
    )
    assert 'mooncen_systemd_unit_active{unit="mooncen-staging-apply.timer"}' in output


def test_cloud_app_and_database_monitoring_stays_separate_from_crawler_evidence() -> None:
    rules = _alert_rules()
    cloud_rules = {
        "mooncen_cloud_deploy_missing",
        "mooncen_cloud_not_primary",
        "mooncen_cloud_app_unit_down",
    }
    assert cloud_rules <= rules.keys()
    for uid in cloud_rules:
        expression = _prometheus_expression(rules[uid])
        assert '{node="cloud"' in expression
        assert "mooncen_crawler_" not in expression

    crawler_rules = {
        "mooncen_gen1crawler_crawler_unhealthy",
        "mooncen_gen1crawler_crawler_down",
    }
    for uid in crawler_rules:
        serialized = json.dumps(rules[uid], ensure_ascii=False)
        assert 'node=\\"gen1crawler\\"' in serialized
        assert 'node=\\"cloud\\"' not in serialized
        assert rules[uid]["labels"]["instance"] == "gen1crawler"
    assert {
        "mooncen_cloud_crawler_unhealthy",
        "mooncen_cloud_crawler_down",
    } <= _deleted_alert_rule_uids()


def test_exporter_installer_includes_cloud_gen1crawler_and_gen1db_without_retired_n100() -> None:
    installer = (MONITORING / "install_exporters.ps1").read_text(encoding="utf-8")
    assert '[string]$CloudHost = "cloud"' in installer
    assert '[string]$Gen1CrawlerHost = "gen1crawler"' in installer
    assert '[string]$Gen1DbHost = "gen1db"' in installer
    assert 'Invoke-RemoteLinuxInstall -Name "cloud"' in installer
    assert 'Invoke-RemoteLinuxInstall -Name "gen1crawler"' in installer
    assert 'Invoke-RemoteLinuxInstall -Name "gen1db"' in installer
    assert "n100" not in installer.lower()


def test_gen1db_control_dashboard_and_alert_use_only_reviewed_metric_contract() -> None:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    expected_panels = {
        "Control Queue",
        "Control Workers",
        "Control Batch",
        "Control & Staging Health",
    }
    assert expected_panels <= panels.keys()

    expressions = "\n".join(
        target["expr"]
        for title in expected_panels
        for target in panels[title].get("targets", [])
    )
    for metric in (
        "mooncen_crawler_control_queue_jobs",
        "mooncen_crawler_control_workers",
        "mooncen_crawler_control_release_reports",
        "mooncen_crawler_control_latest_batch_outcome",
        "mooncen_crawler_control_collector_success",
        "mooncen_crawler_control_generated_timestamp_seconds",
        "mooncen_crawler_control_expired_leases",
        "mooncen_postgres_in_recovery",
        "mooncen-crawler-control-metrics.timer",
        "mooncen-staging-apply.timer",
    ):
        assert metric in expressions
    assert expressions.count('{node="gen1db"') >= 10
    for forbidden in ("provider", "worker_key", "job_id", "error_message", "payload"):
        assert forbidden not in expressions

    rule = _alert_rules()["mooncen_gen1db_control_unhealthy"]
    expression = _prometheus_expression(rule)
    for metric in (
        "mooncen_postgres_in_recovery",
        "mooncen_crawler_control_collector_success",
        "mooncen_crawler_control_generated_timestamp_seconds",
        "mooncen_crawler_control_expired_leases",
        "mooncen_crawler_control_queue_jobs",
        "mooncen_crawler_control_workers",
        "mooncen_crawler_control_latest_batch_outcome",
        "mooncen_crawler_control_release_reports",
    ):
        assert metric in expression
    recurring_units = (
        "mooncen-crawler-control-scheduler.service",
        "mooncen-crawler-control-finalizer.service",
        "mooncen-crawler-release-publisher.timer",
        "mooncen-crawler-control-metrics.timer",
        "mooncen-staging-apply.timer",
    )
    one_shot_units = (
        "mooncen-crawler-release-publisher.service",
        "mooncen-crawler-control-metrics.service",
        "mooncen-staging-apply.service",
    )
    for unit in recurring_units:
        assert expression.count(unit) == 2
        assert unit in expressions
    for unit in one_shot_units:
        assert unit in expression
        assert unit in expressions
    assert "mooncen_systemd_unit_active" in expression
    assert "mooncen_systemd_unit_enabled" in expression
    assert "mooncen_systemd_unit_result_failed" in expression
    assert "!= bool 5" in expression
    assert "!= bool 3" in expression
    assert '(up{node="gen1db",alerting="enabled"} == bool 1)' in expression
    assert "absent(mooncen_crawler_control_collector_success" in expression
    assert "dead_lettered" in expression
    assert "failed|drifted" in expression
    assert rule["noDataState"] == "OK"
    assert rule["labels"]["node"] == "gen1db"
    stale_expression = _prometheus_expression(_alert_rules()["mooncen_metrics_stale"])
    assert (
        'node=~"cloud|gen1crawler|gen1db",alerting="enabled"'
        in stale_expression
    )
