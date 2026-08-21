import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from monitor_app import app as monitor


HEALTHY_BACKUP_ROW = {
    "node": "cloud",
    "name": "mooncen-backup.timer",
    "active": True,
    "fresh": True,
    "fresh_known": True,
    "health": "healthy",
    "freshness_policy": "last trigger must be on or after yesterday 00:00 KST",
}


def core_snapshot(service_statuses=None, primary_status="healthy"):
    service_statuses = service_statuses or {}
    generated_at = "2026-08-07T00:00:00+00:00"
    topology = {
        "environment": "production",
        "active_node": "cloud",
        "service_nodes": {
            "database": "cloud",
            "frontend": "cloud",
            "backend": "cloud",
            "crawler": "cloud",
        },
        "crawler_mode": "legacy",
        "crawler_runtime_node": "cloud",
        "crawler_target_node": "gen1crawler",
        "crawler_control_node": "gen1db",
        "crawler_transition_state": "cutover_pending",
        "crawler_runtime_drift": True,
    }
    primary = {
        "node": "cloud" if primary_status == "healthy" else None,
        "expected_node": "cloud",
        "status": primary_status,
        "ok": primary_status == "healthy",
        "role_ok": True if primary_status == "healthy" else None,
        "database_writable": True if primary_status == "healthy" else None,
        "candidates": ["cloud"] if primary_status == "healthy" else [],
        "matches_topology": True if primary_status == "healthy" else None,
    }
    rows = []
    for service in monitor.CORE_SERVICE_ORDER:
        status = service_statuses.get(service, "healthy")
        functional_ok = True if status == "healthy" else False if status == "critical" else None
        service_node = topology["service_nodes"][service]
        rows.append({
            "service": service,
            "label": monitor.CORE_SERVICE_LABELS[service],
            "node": service_node,
            "primary_node": service_node,
            "active_nodes": [service_node],
            "runtime_ok": True,
            "functional_ok": functional_ok,
            "ok": status == "healthy",
            "status": status,
            "detail": f"{service} {status}",
            "checked_at": generated_at,
        })
    return monitor.core_snapshot_payload(topology, primary, rows, generated_at)


def production_quality_payload(**count_overrides):
    counts = {
        "active_courses": 125,
        "missing_required": 3,
        "invalid_dates": 2,
        "invalid_prices": 1,
        "missing_address": 4,
        "missing_coordinates": 5,
        "incomplete_location": 6,
        "out_of_korea": 0,
        "duplicate_urls": 7,
        "blocked_sync": 8,
    }
    counts.update(count_overrides)
    return {
        "schema_version": 1,
        "generated_at": "2026-08-15T05:00:00Z",
        "available": True,
        "source": "production_database",
        "counts": counts,
        "issue_statuses": [
            {"status": "status-0", "severity": "warning", "issue_count": 9},
        ],
        "latest_scan_at": "2026-08-15T04:55:00Z",
        "rule_source": "production courses/service_group",
    }


class MonitorApiTest(unittest.TestCase):
    def setUp(self):
        monitor.app.config.update(TESTING=True)
        self.token_patcher = mock.patch.object(monitor, "APP_TOKEN", "test-secret")
        self.token_patcher.start()
        self.addCleanup(self.token_patcher.stop)
        self.client = monitor.app.test_client()
        self.auth_headers = {"X-App-Token": "test-secret"}
        monitor.clear_core_snapshot_cache()
        monitor.clear_crawler_quality_cache()

    def _summary_with_backups(self, backup_rows):
        with (
            mock.patch.object(monitor, "get_server_rows", return_value=[]),
            mock.patch.object(monitor, "get_scrape_targets", return_value=[]),
            mock.patch.object(monitor, "get_prometheus_alerts", return_value=[]),
            mock.patch.object(monitor, "get_cached_core_snapshot", return_value=core_snapshot()),
            mock.patch.object(monitor, "backup_status", return_value=backup_rows),
        ):
            return self.client.get(
                "/api/monitoring/summary",
                headers=self.auth_headers,
            ).get_json()

    def test_api_requires_configured_token(self):
        response = self.client.get("/api/operation/actions")
        self.assertEqual(401, response.status_code)

        with mock.patch.object(monitor, "APP_TOKEN", ""):
            response = self.client.get(
                "/api/operation/actions",
                headers=self.auth_headers,
            )
        self.assertEqual(503, response.status_code)

        response = self.client.get(
            "/api/operation/actions?token=test-secret",
        )
        self.assertEqual(401, response.status_code)

        response = self.client.get(
            "/api/operation/actions",
            headers=self.auth_headers,
        )
        self.assertEqual(404, response.status_code)

    def test_summary_reports_healthy_without_grafana(self):
        with (
            mock.patch.object(
                monitor,
                "get_server_rows",
                return_value=[{
                    "node": "bot",
                    "up": "UP",
                    "cpu": "10.0%",
                    "mem": "20.0%",
                    "disk": "30.0%",
                    "temp": "40C",
                    "uptime": "1d 0h",
                    "role": "monitoring",
                    "alerting": "enabled",
                }],
            ),
            mock.patch.object(
                monitor,
                "get_scrape_targets",
                return_value=[{
                    "node": "bot",
                    "job": "node_exporter",
                    "instance": "bot:9100",
                    "health": "up",
                    "role": "monitoring",
                    "alerting": "enabled",
                    "last_error": "",
                }],
            ),
            mock.patch.object(
                monitor,
                "get_cached_core_snapshot",
                return_value=core_snapshot(),
            ),
            mock.patch.object(monitor, "get_prometheus_alerts", return_value=[]),
            mock.patch.object(
                monitor,
                "backup_status",
                return_value=[HEALTHY_BACKUP_ROW],
            ),
        ):
            response = self.client.get(
                "/api/monitoring/summary",
                headers=self.auth_headers,
            )

        self.assertEqual(200, response.status_code)
        data = response.get_json()
        self.assertEqual("healthy", data["status"])
        self.assertEqual("정상", data["status_label"])
        self.assertEqual([], data["problems"])
        self.assertNotIn("grafana_url", data)
        self.assertEqual(1, data["counts"]["servers"])
        self.assertEqual(4, len(data["core_services"]))
        self.assertEqual(data["core_services"], data["services"])
        self.assertEqual("cloud", data["primary"]["node"])

    def test_summary_builds_actionable_problem_list(self):
        with (
            mock.patch.object(
                monitor,
                "get_server_rows",
                return_value=[{
                    "node": "nas",
                    "up": "DOWN",
                    "cpu": "-",
                    "mem": "-",
                    "disk": "-",
                    "temp": "-",
                    "uptime": "-",
                    "role": "storage",
                    "alerting": "enabled",
                }],
            ),
            mock.patch.object(
                monitor,
                "get_scrape_targets",
                return_value=[{
                    "node": "monitor",
                    "job": "mooncen_ops",
                    "instance": "monitor:8088",
                    "health": "down",
                    "role": "storage",
                    "alerting": "enabled",
                    "last_error": "connection refused",
                }],
            ),
            mock.patch.object(
                monitor,
                "get_cached_core_snapshot",
                return_value=core_snapshot({"backend": "critical"}),
            ),
            mock.patch.object(
                monitor,
                "get_prometheus_alerts",
                return_value=[{
                    "source": "prometheus",
                    "state": "firing",
                    "name": "DiskFull",
                    "summary": "disk full",
                    "labels": {"severity": "warning"},
                }],
            ),
            mock.patch.object(
                monitor,
                "backup_status",
                return_value=[HEALTHY_BACKUP_ROW],
            ),
        ):
            response = self.client.get(
                "/api/monitoring/summary",
                headers=self.auth_headers,
            )

        data = response.get_json()
        self.assertEqual("critical", data["status"])
        self.assertEqual(1, data["counts"]["down_servers"])
        self.assertEqual(1, data["counts"]["down_targets"])
        self.assertEqual(1, data["counts"]["failing_services"])
        self.assertEqual(1, data["counts"]["active_alerts"])
        self.assertEqual(0, data["counts"]["mooncen_failures"])
        self.assertEqual(1, len(data["problems"]))
        self.assertEqual("service:backend", data["problems"][0]["key"])
        self.assertNotIn("server", {item["kind"] for item in data["problems"]})
        self.assertNotIn("alert", {item["kind"] for item in data["problems"]})

    def test_non_core_collection_error_does_not_change_core_status(self):
        with (
            mock.patch.object(monitor, "get_server_rows", side_effect=RuntimeError("prometheus down")),
            mock.patch.object(monitor, "get_scrape_targets", return_value=[]),
            mock.patch.object(monitor, "get_cached_core_snapshot", return_value=core_snapshot()),
            mock.patch.object(monitor, "get_prometheus_alerts", return_value=[]),
            mock.patch.object(monitor, "backup_status", return_value=[HEALTHY_BACKUP_ROW]),
        ):
            response = self.client.get(
                "/api/monitoring/summary",
                headers=self.auth_headers,
            )

        data = response.get_json()
        self.assertEqual("healthy", data["status"])
        self.assertEqual([], data["problems"])
        self.assertTrue(any("prometheus down" in error for error in data["errors"]))

    def test_summary_reports_backup_evidence_without_changing_core_status(self):
        stale_backup = {
            **HEALTHY_BACKUP_ROW,
            "active": False,
            "fresh": False,
            "health": "stale",
            "last_triggered_at": "2026-07-20T18:30:00+00:00",
        }
        data = self._summary_with_backups([stale_backup])
        self.assertEqual("healthy", data["status"])
        self.assertEqual(1, data["counts"]["backup_stale"])
        self.assertEqual(0, data["counts"]["backup_error"])
        self.assertEqual(0, data["counts"]["backup_unknown"])
        self.assertEqual([], data["problems"])
        self.assertEqual("stale", data["backup"]["health"])

        invalid_backup = {
            **stale_backup,
            "fresh_known": False,
            "health": "error",
        }
        data = self._summary_with_backups([invalid_backup])
        self.assertEqual("healthy", data["status"])
        self.assertEqual(0, data["counts"]["backup_stale"])
        self.assertEqual(1, data["counts"]["backup_error"])
        self.assertEqual([], data["problems"])

    def test_summary_keeps_missing_backup_as_diagnostic_only(self):
        data = self._summary_with_backups([])
        self.assertEqual("healthy", data["status"])
        self.assertEqual([], data["errors"])
        self.assertEqual(0, data["counts"]["backup_stale"])
        self.assertEqual(0, data["counts"]["backup_error"])
        self.assertEqual(1, data["counts"]["backup_unknown"])
        self.assertEqual([], data["problems"])

    def test_server_rows_are_built_from_raw_prometheus_metrics(self):
        def query_result(query):
            if query.startswith('up{node!=""'):
                return [{
                    "metric": {
                        "node": "bot",
                        "role": "monitoring",
                        "alerting": "enabled",
                    },
                    "value": [1, "1"],
                }]
            value = "42"
            if "boot_time" in query:
                value = "90000"
            return [{"metric": {"node": "bot"}, "value": [1, value]}]

        with mock.patch.object(
            monitor,
            "query_vector",
            side_effect=query_result,
        ) as query_vector:
            rows = monitor.get_server_rows()

        self.assertEqual(1, len(rows))
        self.assertEqual("UP", rows[0]["up"])
        self.assertEqual("42.0%", rows[0]["cpu"])
        self.assertEqual("42C", rows[0]["temp"])
        self.assertEqual("1d 1h", rows[0]["uptime"])
        queried_promql = [call.args[0] for call in query_vector.call_args_list]
        self.assertTrue(
            any("node_thermal_temperature_celsius" in query for query in queried_promql)
        )
        self.assertTrue(
            any("node_memory_inactive_bytes" in query for query in queried_promql)
        )

    def test_server_rows_reject_invalid_or_offline_temperature_evidence(self):
        invalid_values = (None, "NaN", "Infinity", "-21", "131")

        for invalid_value in invalid_values:
            with self.subTest(value=invalid_value):
                def query_result(query):
                    if query.startswith('up{node!=""'):
                        return [{
                            "metric": {"node": "bot"},
                            "value": [1, "1"],
                        }]
                    value = invalid_value if "temperature" in query else "42"
                    return [{"metric": {"node": "bot"}, "value": [1, value]}]

                with mock.patch.object(monitor, "query_vector", side_effect=query_result):
                    rows = monitor.get_server_rows()

                self.assertEqual("-", rows[0]["temp"])

        def offline_result(query):
            if query.startswith('up{node!=""'):
                return [{"metric": {"node": "bot"}, "value": [1, "0"]}]
            value = "55" if "temperature" in query else "42"
            return [{"metric": {"node": "bot"}, "value": [1, value]}]

        with mock.patch.object(monitor, "query_vector", side_effect=offline_result):
            rows = monitor.get_server_rows()

        self.assertEqual("DOWN", rows[0]["up"])
        self.assertEqual("-", rows[0]["temp"])

    def test_core_topology_separates_current_runtime_target_and_control_nodes(self):
        topology = monitor.get_core_topology()

        self.assertEqual("cloud", topology["active_node"])
        self.assertEqual("legacy", topology["crawler_mode"])
        self.assertEqual("cloud", topology["service_nodes"]["database"])
        self.assertEqual("cloud", topology["service_nodes"]["frontend"])
        self.assertEqual("cloud", topology["service_nodes"]["backend"])
        self.assertEqual("cloud", topology["service_nodes"]["crawler"])
        self.assertEqual("cloud", topology["crawler_runtime_node"])
        self.assertEqual("gen1crawler", topology["crawler_target_node"])
        self.assertEqual("gen1db", topology["crawler_control_node"])
        self.assertEqual("cutover_pending", topology["crawler_transition_state"])
        self.assertTrue(topology["crawler_runtime_drift"])

    def test_core_topology_marks_target_runtime_only_when_runtime_matches_target(self):
        with mock.patch.object(
            monitor,
            "CORE_CRAWLER_RUNTIME_NODE",
            monitor.CORE_CRAWLER_TARGET_NODE,
        ):
            topology = monitor.get_core_topology()

        self.assertEqual("gen1crawler", topology["service_nodes"]["crawler"])
        self.assertEqual("gen1crawler", topology["crawler_runtime_node"])
        self.assertEqual("gen1crawler", topology["crawler_target_node"])
        self.assertEqual("target_runtime", topology["crawler_transition_state"])
        self.assertFalse(topology["crawler_runtime_drift"])

    def test_prometheus_inventory_classifies_target_worker_and_control_as_pending(self):
        inventory = (
            Path(__file__).resolve().parent.parent / "prometheus.remote.yml"
        ).read_text(encoding="utf-8")

        self.assertIn(
            """      - targets:
          - gen1crawler:9100
        labels:
          node: gen1crawler
          role: crawler-worker
          alerting: pending""",
            inventory,
        )
        self.assertIn(
            """      - targets:
          - gen1db:9100
        labels:
          node: gen1db
          role: crawler-control
          alerting: pending""",
            inventory,
        )
        self.assertNotIn("candidate-crawler", inventory)

    def test_core_runtime_checks_exactly_four_services_and_current_cloud_timer(self):
        with mock.patch.object(
            monitor,
            "first_value",
            return_value=1,
        ) as first_value:
            topology = monitor.get_core_topology()
            runtime = monitor.get_core_runtime_status(topology)

        self.assertEqual(list(monitor.CORE_SERVICE_ORDER), list(runtime))
        self.assertTrue(runtime["crawler"]["runtime_ok"])
        crawler_queries = [
            call.args[0]
            for call in first_value.call_args_list
            if "mooncen-crawler.timer" in call.args[0]
        ]
        self.assertEqual(1, len(crawler_queries))
        self.assertIn('node="cloud"', crawler_queries[0])
        self.assertIn("mooncen-crawler.timer", crawler_queries[0])
        self.assertNotIn("mooncen-staging-apply.timer", crawler_queries[0])
        self.assertNotIn("mooncen-crawler-once.service", crawler_queries[0])
        self.assertNotIn("gen1crawler", crawler_queries[0])
        self.assertNotIn("gen1db", crawler_queries[0])

    def test_crawler_latest_snapshot_uses_only_observed_durable_metrics(self):
        completed_at = 2_000_000.0

        def metric_value(query):
            if "state_valid" in query:
                return 1
            if "last_completion" in query:
                return completed_at
            if "providers_requested" in query:
                return 12
            if "providers_completed" in query:
                return 10
            if "providers_failed" in query:
                return 2
            if 'outcome="partial_success"' in query:
                return 1
            if "cycle_outcome" in query:
                return 0
            return None

        with mock.patch.object(monitor, "first_value", side_effect=metric_value) as first_value:
            latest, errors = monitor.crawler_latest_snapshot("cloud")

        self.assertTrue(latest["available"])
        self.assertEqual("partial_success", latest["status"])
        self.assertEqual(12, latest["providers_requested"])
        self.assertEqual(10, latest["providers_succeeded"])
        self.assertEqual(2, latest["providers_failed"])
        self.assertIsNone(latest["duration_seconds"])
        self.assertIsNone(latest["collected_count"])
        self.assertIsNone(latest["new_count"])
        self.assertIsNone(latest["updated_count"])
        self.assertIsNone(latest["skipped_count"])
        self.assertEqual([], errors)
        self.assertTrue(all('node="cloud"' in call.args[0] for call in first_value.call_args_list))

    def test_crawler_latest_snapshot_exposes_running_and_last_success(self):
        now = 2_000_100.0

        def metric_value(query):
            if "crawler_last_success" in query:
                return now - 60
            if "timer_last_trigger" in query:
                return now - 20
            if 'state=~"active|activating"' in query:
                return 1
            if "unit_result_failed" in query:
                return 0
            if "state_valid" in query:
                return None
            if "cycle_outcome" in query:
                return None
            return None

        with mock.patch.object(monitor, "first_value", side_effect=metric_value):
            latest, errors = monitor.crawler_latest_snapshot("cloud", now=now)

        self.assertTrue(latest["available"])
        self.assertTrue(latest["running"])
        self.assertEqual("running", latest["status"])
        self.assertEqual(60.0, latest["last_success_age_seconds"])
        self.assertEqual(20.0, latest["duration_seconds"])
        self.assertIsNotNone(latest["last_success_at"])
        self.assertIsNotNone(latest["started_at"])
        self.assertNotIn("completion_timestamp_unavailable", str(errors))

    def test_crawler_latest_snapshot_fails_closed_without_prometheus_evidence(self):
        with mock.patch.object(monitor, "first_value", return_value=None):
            latest, errors = monitor.crawler_latest_snapshot("cloud")

        self.assertFalse(latest["available"])
        self.assertEqual("unknown", latest["status"])
        self.assertIsNone(latest["completed_at"])
        self.assertIsNone(latest["providers_requested"])
        self.assertGreaterEqual(len(errors), 3)

    def test_crawler_ops_snapshot_does_not_invent_unavailable_statistics(self):
        with mock.patch.object(
            monitor,
            "get_ops_crawler_summary",
            return_value={"available": False, "error": "private upstream detail"},
        ):
            summary, providers, errors = monitor.crawler_ops_snapshot()

        self.assertFalse(summary["available"])
        self.assertIsNone(summary["has_data"])
        for key in (
            "run_count",
            "success_count",
            "partial_count",
            "failure_count",
            "in_progress_count",
            "collected_count",
            "processed_count",
            "new_count",
            "updated_count",
            "skipped_count",
            "avg_duration_seconds",
        ):
            self.assertIsNone(summary[key], key)
        self.assertEqual([], providers["items"])
        self.assertIsNone(providers["total"])
        self.assertNotIn("private upstream detail", str(errors))

    def test_crawler_ops_snapshot_normalizes_bounded_analytics_sections(self):
        upstream = {
            "available": True,
            "collection": {
                "components": {
                    "runs": {
                        "available": True,
                        "has_data": True,
                        "totals": {
                            "run_count": 4,
                            "successful_runs": 2,
                            "partial_runs": 1,
                            "failed_runs": 1,
                            "in_progress_runs": 0,
                            "collected_count": 80,
                            "processed_count": 78,
                            "new_count": 12,
                            "updated_count": 30,
                            "last_run_at": "2026-08-12T11:00:00Z",
                        },
                    }
                }
            },
            "providers": {
                "components": {
                    "collection": {
                        "available": True,
                        "has_data": True,
                        "total": 1,
                        "items": [{
                            "provider": "EMART",
                            "run_count": 2,
                            "successful_runs": 2,
                            "partial_runs": 0,
                            "failed_runs": 0,
                            "collected_count": 40,
                            "new_count": 5,
                            "updated_count": 15,
                            "failed_item_count": 0,
                            "success_rate": 100.0,
                            "last_run_at": "2026-08-12T10:00:00Z",
                        }],
                    }
                }
            },
        }
        with mock.patch.object(monitor, "get_ops_crawler_summary", return_value=upstream):
            summary, providers, errors = monitor.crawler_ops_snapshot()

        self.assertTrue(summary["available"])
        self.assertEqual(4, summary["run_count"])
        self.assertEqual(2, summary["success_count"])
        self.assertEqual(1, summary["partial_count"])
        self.assertEqual(1, summary["failure_count"])
        self.assertEqual(80, summary["collected_count"])
        self.assertEqual(12, summary["new_count"])
        self.assertEqual(30, summary["updated_count"])
        self.assertIsNone(summary["skipped_count"])
        self.assertIsNone(summary["avg_duration_seconds"])
        self.assertTrue(providers["available"])
        self.assertEqual("EMART", providers["items"][0]["provider"])
        self.assertEqual(100.0, providers["items"][0]["success_rate"])
        self.assertEqual([], errors)

    def test_crawler_quality_snapshot_uses_distinct_server_token_and_fixed_path(self):
        server_token = "server-monitor-test-token-0123456789"
        response = mock.Mock(status_code=200)
        response.json.return_value = production_quality_payload()
        with (
            mock.patch.object(monitor, "MOONCEN_SERVER_MONITOR_TOKEN", server_token),
            mock.patch.object(
                monitor,
                "MOONCEN_SERVER_MONITOR_BASE_URL",
                "https://mooncen.kr",
            ),
            mock.patch.object(
                monitor,
                "MOONCEN_SERVER_MONITOR_TIMEOUT_SECONDS",
                20.0,
            ),
            mock.patch.object(monitor.requests, "get", return_value=response) as request_get,
        ):
            quality = monitor.crawler_quality_snapshot()

        self.assertTrue(quality["available"])
        self.assertEqual("production_database", quality["source"])
        self.assertEqual(125, quality["counts"]["active_courses"])
        self.assertEqual(0, quality["counts"]["out_of_korea"])
        self.assertEqual("status-0", quality["issue_statuses"][0]["status"])
        self.assertEqual("2026-08-15T04:55:00Z", quality["latest_scan_at"])
        self.assertIsNone(quality["reason_code"])
        request_get.assert_called_once_with(
            "https://mooncen.kr/api/monitoring/crawler-quality",
            headers={"X-MoonCen-Monitor-Token": server_token},
            timeout=20.0,
            allow_redirects=False,
        )

    def test_crawler_quality_snapshot_without_token_fails_closed_without_request(self):
        with (
            mock.patch.object(monitor, "MOONCEN_SERVER_MONITOR_TOKEN", ""),
            mock.patch.object(monitor.requests, "get") as request_get,
        ):
            quality = monitor.crawler_quality_snapshot()

        self.assertFalse(quality["available"])
        self.assertEqual("server_monitor_token_not_configured", quality["reason_code"])
        self.assertTrue(all(value is None for value in quality["counts"].values()))
        self.assertEqual([], quality["issue_statuses"])
        self.assertIsNone(quality["latest_scan_at"])
        request_get.assert_not_called()

        with (
            mock.patch.object(monitor, "MOONCEN_SERVER_MONITOR_TOKEN", "too-short"),
            mock.patch.object(monitor.requests, "get") as invalid_request_get,
        ):
            invalid = monitor.crawler_quality_snapshot()
        self.assertFalse(invalid["available"])
        self.assertEqual("server_monitor_token_invalid", invalid["reason_code"])
        invalid_request_get.assert_not_called()

    def test_crawler_quality_invalid_contract_never_turns_missing_values_into_zero(self):
        upstream = production_quality_payload()
        upstream["counts"]["missing_required"] = "0"

        quality = monitor.normalize_crawler_quality_snapshot(upstream)

        self.assertFalse(quality["available"])
        self.assertEqual("server_monitor_response_invalid", quality["reason_code"])
        self.assertTrue(all(value is None for value in quality["counts"].values()))
        self.assertEqual([], quality["issue_statuses"])

        boolean_schema = production_quality_payload()
        boolean_schema["schema_version"] = True
        boolean_quality = monitor.normalize_crawler_quality_snapshot(boolean_schema)
        self.assertFalse(boolean_quality["available"])
        self.assertEqual(
            "server_monitor_response_invalid",
            boolean_quality["reason_code"],
        )

    def test_server_monitor_timeout_setting_is_bounded(self):
        for raw, expected in (("0", 1.0), ("20", 20.0), ("99", 30.0), ("bad", 20.0)):
            with self.subTest(raw=raw), mock.patch.dict(
                os.environ,
                {"MOONCEN_SERVER_MONITOR_TIMEOUT_SECONDS": raw},
            ):
                self.assertEqual(
                    expected,
                    monitor.bounded_env_number(
                        "MOONCEN_SERVER_MONITOR_TIMEOUT_SECONDS",
                        20.0,
                        1.0,
                        30.0,
                        float,
                    ),
                )

    def test_crawler_quality_cache_deduplicates_refresh_and_does_not_block_poll(self):
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def slow_quality():
            started.set()
            release.wait(2.0)
            return monitor.normalize_crawler_quality_snapshot(production_quality_payload())

        with (
            mock.patch.object(
                monitor,
                "CRAWLER_QUALITY_INITIAL_WAIT_SECONDS",
                0.0,
            ),
            mock.patch.object(
                monitor,
                "crawler_quality_snapshot",
                side_effect=slow_quality,
            ) as quality_fetch,
        ):
            first = monitor.get_cached_crawler_quality_snapshot()
            self.assertTrue(started.wait(1.0))
            second = monitor.get_cached_crawler_quality_snapshot()

            self.assertFalse(first["available"])
            self.assertFalse(second["available"])
            self.assertEqual("quality_refresh_pending", first["reason_code"])
            self.assertEqual(1, quality_fetch.call_count)
            release.set()
            with monitor._CRAWLER_QUALITY_CACHE_LOCK:
                refresh_event = monitor._CRAWLER_QUALITY_CACHE["event"]
            self.assertTrue(refresh_event.wait(1.0))
            refreshed = monitor.get_cached_crawler_quality_snapshot()
            self.assertTrue(refreshed["available"])
            self.assertEqual(1, quality_fetch.call_count)

    def test_optional_quality_does_not_make_crawler_root_available(self):
        unavailable_latest = {
            "available": False,
            "status": "unknown",
        }
        unavailable_summary = {
            "available": False,
        }
        unavailable_providers = {
            "available": False,
        }
        quality = monitor.normalize_crawler_quality_snapshot(production_quality_payload())
        collected = {
            "latest": (unavailable_latest, []),
            "operations": (unavailable_summary, unavailable_providers, []),
            "nodes": ([], []),
            "quality": quality,
        }
        with (
            mock.patch.object(
                monitor,
                "get_cached_core_snapshot",
                return_value=core_snapshot(),
            ),
            mock.patch.object(
                monitor,
                "collect_parallel",
                return_value=(collected, {}),
            ),
        ):
            snapshot = monitor.collect_crawler_monitoring_snapshot()

        self.assertTrue(snapshot["quality"]["available"])
        self.assertFalse(snapshot["available"])
        self.assertFalse(snapshot["complete"])
        self.assertFalse(snapshot["partial"])

    def test_crawler_node_snapshots_include_runtime_performance_and_custom_temperature(self):
        topology = monitor.get_core_topology()

        def node_values(query):
            if query.startswith("up{"):
                return {"cloud": "1", "gen1crawler": "1", "gen1db": "0"}
            if query.startswith("count by (node)"):
                return {"cloud": "2", "gen1crawler": "4", "gen1db": "8"}
            if "node_cpu_seconds_total" in query:
                return {"cloud": "12.5", "gen1crawler": "25"}
            if "node_memory_MemAvailable_bytes" in query:
                return {"cloud": "40", "gen1crawler": "55", "gen1db": "60"}
            if "node_filesystem_avail_bytes" in query:
                return {"cloud": "18", "gen1crawler": "22", "gen1db": "30"}
            if query.startswith("node_load1"):
                return {"cloud": "1.5", "gen1crawler": "0.5", "gen1db": "0.25"}
            if "mooncen_hardware_temperature_celsius" in query:
                return {"cloud": "51.25"}
            return {}

        with mock.patch.object(
            monitor,
            "query_values_by_node",
            side_effect=node_values,
        ) as query_values:
            nodes, errors = monitor.crawler_node_snapshots(topology)

        by_role = {row["role"]: row for row in nodes}
        self.assertEqual("cloud", by_role["runtime"]["node"])
        self.assertEqual("up", by_role["runtime"]["status"])
        self.assertEqual(12.5, by_role["runtime"]["cpu_percent"])
        self.assertEqual(40.0, by_role["runtime"]["memory_percent"])
        self.assertEqual(51.25, by_role["runtime"]["temp_celsius"])
        self.assertTrue(by_role["runtime"]["temperature_available"])
        self.assertEqual(18.0, by_role["runtime"]["disk_percent"])
        self.assertEqual(1.5, by_role["runtime"]["load_1m"])
        self.assertEqual(2, by_role["runtime"]["logical_cpu_count"])
        self.assertEqual("gen1crawler", by_role["target"]["node"])
        self.assertEqual("gen1db", by_role["control"]["node"])
        self.assertEqual("down", by_role["control"]["status"])
        self.assertEqual([], errors)
        temperature_queries = [
            call.args[0]
            for call in query_values.call_args_list
            if "temperature" in call.args[0]
        ]
        self.assertEqual(1, len(temperature_queries))
        self.assertLess(
            temperature_queries[0].index("mooncen_hardware_temperature_celsius"),
            temperature_queries[0].index("node_hwmon_temp_celsius"),
        )
        self.assertIn("mooncen_temperature_collector_success", temperature_queries[0])
        self.assertIn("mooncen_temperature_sensor_count", temperature_queries[0])
        self.assertIn("mooncen_temperature_collector_timestamp_seconds", temperature_queries[0])
        self.assertIn("windows_exporter", temperature_queries[0])
        self.assertIn("<= 300", temperature_queries[0])
        self.assertIn(">= -60", temperature_queries[0])

    def test_crawler_monitoring_endpoint_returns_the_strict_snapshot(self):
        snapshot = {
            "schema_version": 1,
            "generated_at": "2026-08-12T14:00:00+00:00",
            "available": True,
            "complete": False,
            "partial": True,
            "status": "warning",
            "topology": monitor.get_core_topology(),
            "latest": {"available": True, "running": True, "status": "running"},
            "summary_24h": {"available": False, "reasons": [{"code": "unavailable"}]},
            "providers": {"available": False, "reasons": [{"code": "unavailable"}]},
            "nodes": [],
            "errors": [],
        }
        with mock.patch.object(
            monitor, "collect_crawler_monitoring_snapshot", return_value=snapshot
        ):
            response = self.client.get(
                "/api/monitoring/crawler", headers=self.auth_headers
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual(snapshot, response.get_json())

    def test_temperature_query_requires_fresh_successful_up_collector(self):
        query = monitor.temperature_promql('node!=""')

        self.assertIn("mooncen_hardware_temperature_celsius", query)
        self.assertIn("mooncen_temperature_collector_success", query)
        self.assertIn("mooncen_temperature_sensor_count", query)
        self.assertIn("mooncen_temperature_collector_timestamp_seconds", query)
        self.assertIn("job=\"windows_exporter\"", query)
        self.assertIn("up{job=~\"node_exporter|windows_exporter\"", query)
        self.assertIn("<= 300", query)
        self.assertIn(">= -60", query)

    def test_gen1crawler_restart_uses_scoped_helper_as_sgm(self):
        command = monitor.operation_command("restart_gen1crawler_crawler")

        self.assertEqual("sgm@gen1crawler", command[-2])
        self.assertEqual(
            "sudo -n /usr/local/libexec/mooncen-ops-service crawler-once",
            command[-1],
        )
        self.assertNotIn("systemctl", " ".join(command))

    def test_backup_status_uses_cloud_timer_trigger_and_kst_calendar_cutoff(self):
        now = datetime(2026, 7, 29, 14, 59, tzinfo=timezone.utc)  # 23:59 KST
        cutoff = datetime(2026, 7, 27, 15, 0, tzinfo=timezone.utc)  # Jul 28 00:00 KST
        prometheus_rows = [
            {
                "metric": {"node": "cloud", "name": "dpkg-db-backup.timer"},
                "value": [1, str(now.timestamp())],
            },
            {
                "metric": {"node": "cloud", "name": "mooncen-backup.service"},
                "value": [1, str(now.timestamp())],
            },
            {
                "metric": {"node": "cloud", "name": "mooncen-backup.timer"},
                "value": [1, str(cutoff.timestamp())],
            },
        ]
        with mock.patch.object(monitor, "query_vector", return_value=prometheus_rows) as query:
            rows = monitor.backup_status(now=now)

        query.assert_called_once_with(
            'node_systemd_timer_last_trigger_seconds'
            '{node="cloud",name="mooncen-backup.timer"}'
        )
        self.assertEqual(1, len(rows))
        self.assertEqual("mooncen-backup.timer", rows[0]["name"])
        self.assertTrue(rows[0]["fresh"])
        self.assertTrue(rows[0]["active"])
        self.assertEqual("healthy", rows[0]["health"])
        self.assertIsNone(rows[0]["last_success_at"])
        self.assertEqual("timer_trigger", rows[0]["timestamp_kind"])
        self.assertEqual("2026-07-28T00:00:00+09:00", rows[0]["fresh_after_kst"])

        stale_trigger = cutoff.timestamp() - 1
        with mock.patch.object(
            monitor,
            "query_vector",
            return_value=[{
                "metric": {"node": "cloud", "name": "mooncen-backup.timer"},
                "value": [1, str(stale_trigger)],
            }],
        ):
            stale_rows = monitor.backup_status(now=now)
        self.assertFalse(stale_rows[0]["fresh"])
        self.assertFalse(stale_rows[0]["active"])
        self.assertEqual("stale", stale_rows[0]["health"])

    def test_backup_status_missing_or_future_metric_is_not_healthy(self):
        now = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
        with mock.patch.object(monitor, "query_vector", return_value=[]):
            self.assertEqual([], monitor.backup_status(now=now))
        with mock.patch.object(
            monitor,
            "query_vector",
            return_value=[{
                "metric": {"node": "cloud", "name": "mooncen-backup.timer"},
                "value": [1, "NaN"],
            }],
        ):
            self.assertEqual([], monitor.backup_status(now=now))

        with mock.patch.object(
            monitor,
            "query_vector",
            return_value=[{
                "metric": {"node": "cloud", "name": "mooncen-backup.timer"},
                "value": [1, str(now.timestamp() + 301)],
            }],
        ):
            rows = monitor.backup_status(now=now)
        self.assertFalse(rows[0]["fresh"])
        self.assertFalse(rows[0]["fresh_known"])
        self.assertEqual("error", rows[0]["health"])

    def test_mooncen_api_exposes_backup_freshness_summary(self):
        backup_row = {
            "node": "cloud",
            "name": "mooncen-backup.timer",
            "active": True,
            "fresh": True,
            "health": "healthy",
            "freshness_policy": "last trigger must be on or after yesterday 00:00 KST",
        }
        with (
            mock.patch.object(monitor, "get_cached_core_snapshot", return_value=core_snapshot()),
            mock.patch.object(monitor, "backup_status", return_value=[backup_row]),
        ):
            response = self.client.get(
                "/api/monitoring/mooncen",
                headers=self.auth_headers,
            )

        self.assertEqual(200, response.status_code)
        backup = response.get_json()["backup"]
        self.assertTrue(backup["available"])
        self.assertTrue(backup["fresh"])
        self.assertEqual("healthy", backup["health"])
        self.assertEqual([backup_row], backup["items"])
        self.assertEqual(4, len(response.get_json()["core_services"]))

    def test_default_excluded_nodes_are_filtered_from_monitoring_sources(self):
        self.assertTrue(monitor.node_is_excluded("DS1515"))
        self.assertTrue(monitor.node_is_excluded("ds718.example.ts.net"))
        self.assertTrue(monitor.node_is_excluded("ds1515:9100"))
        self.assertTrue(monitor.node_is_excluded("n100"))
        self.assertFalse(monitor.node_is_excluded("VICTUS"))
        self.assertFalse(monitor.node_is_excluded("victus.example.ts.net"))
        self.assertFalse(monitor.node_is_excluded("cloud"))

        def query_result(query):
            if query.startswith('up{node!=""'):
                return [
                    {
                        "metric": {
                            "node": node,
                            "role": "test",
                            "alerting": "enabled",
                        },
                        "value": [1, "1"],
                    }
                    for node in ("ds1515", "ds718", "n100", "victus", "cloud")
                ]
            return [
                {"metric": {"node": node}, "value": [1, "42"]}
                for node in ("ds1515", "ds718", "n100", "victus", "cloud")
            ]

        with mock.patch.object(monitor, "query_vector", side_effect=query_result):
            self.assertEqual(
                ["cloud", "victus"],
                [row["node"] for row in monitor.get_server_rows()],
            )

        targets_payload = {
            "activeTargets": [
                {
                    "labels": {
                        "node": node,
                        "job": "node_exporter",
                        "instance": f"{node}:9100",
                    },
                    "health": "down",
                }
                for node in ("ds1515", "ds718", "n100", "victus", "cloud")
            ]
        }
        with mock.patch.object(monitor, "prometheus_get", return_value=targets_payload):
            self.assertEqual(
                ["cloud", "victus"],
                [row["node"] for row in monitor.get_scrape_targets()],
            )

        alert_payload = {
            "alerts": [
                {
                    "state": "firing",
                    "labels": {
                        "alertname": "TargetDown",
                        "severity": "critical",
                        "node": node,
                    },
                    "annotations": {},
                }
                for node in ("ds1515", "ds718", "n100", "victus", "cloud")
            ]
        }
        with mock.patch.object(monitor, "prometheus_get", return_value=alert_payload):
            alerts = monitor.get_prometheus_alerts()
        self.assertEqual(
            {"cloud", "victus"},
            {row["labels"]["node"] for row in alerts},
        )

        healthy = core_snapshot()
        self.assertEqual(
            [],
            monitor.build_problem_list(healthy["core_services"], healthy["primary"]),
        )

    def test_app_excluded_nodes_environment_adds_to_defaults(self):
        self.assertEqual(
            frozenset({"ds1515", "ds718", "n100", "one", "two"}),
            monitor.configured_excluded_nodes(" ONE, two, ,"),
        )
        self.assertEqual(
            frozenset({"ds1515", "ds718", "n100"}),
            monitor.configured_excluded_nodes(""),
        )

    def test_service_checks_are_exact_core_alias_without_legacy_or_ai(self):
        snapshot = core_snapshot()
        with mock.patch.object(monitor, "get_cached_core_snapshot", return_value=snapshot):
            rows = monitor.get_service_checks()

        self.assertEqual(list(monitor.CORE_SERVICE_ORDER), [row["service"] for row in rows])
        self.assertEqual(4, len(rows))
        self.assertNotIn("ollama", {row["service"] for row in rows})
        self.assertNotIn("cloudflare", {row["service"] for row in rows})

    def test_service_metrics_do_not_emit_legacy_down_for_warning_or_unknown(self):
        snapshot = core_snapshot({
            "frontend": "warning",
            "backend": "unknown",
            "crawler": "critical",
        })
        with mock.patch.object(
            monitor,
            "get_service_checks",
            return_value=snapshot["core_services"],
        ):
            lines = monitor.build_service_metrics()

        legacy = [line for line in lines if line.startswith("mooncen_service_up{")]
        statuses = [line for line in lines if line.startswith("mooncen_core_service_status{")]
        self.assertEqual(2, len(legacy))
        self.assertEqual(16, len(statuses))
        self.assertEqual(
            4,
            sum(line.endswith(" 1.0") for line in statuses),
        )
        self.assertFalse(any('service="frontend"' in line for line in legacy))
        self.assertFalse(any('service="backend"' in line for line in legacy))
        self.assertTrue(any('service="crawler"' in line and line.endswith(" 0.0") for line in legacy))

    def test_core_problem_key_is_logical_and_stable_across_primary_nodes(self):
        first = core_snapshot({"crawler": "critical"})
        services = first["core_services"]
        services[-1] = {**services[-1], "node": "gen1crawler", "primary_node": "gen1crawler"}
        problems = monitor.build_problem_list(services, first["primary"])

        self.assertEqual(1, len(problems))
        self.assertEqual("service:crawler", problems[0]["key"])

    def test_unknown_core_service_and_primary_are_warnings(self):
        snapshot = core_snapshot({"frontend": "unknown"}, primary_status="unknown")
        self.assertEqual("warning", snapshot["status"])
        self.assertEqual([], snapshot["problems"])
        self.assertEqual(2, snapshot["counts"]["warning"])

    def test_core_service_status_combines_functional_and_runtime_evidence(self):
        topology = core_snapshot()["topology"]
        checked_at = "2026-08-07T00:00:00+00:00"
        cases = (
            (True, True, "healthy"),
            (True, False, "warning"),
            (True, None, "warning"),
            (False, True, "critical"),
            (None, True, "unknown"),
        )
        for functional_ok, runtime_ok, expected_status in cases:
            with self.subTest(functional_ok=functional_ok, runtime_ok=runtime_ok):
                row = monitor.core_service_row(
                    "backend",
                    topology,
                    {"runtime_ok": runtime_ok, "active_nodes": []},
                    {"ok": functional_ok, "detail": "test"},
                    checked_at,
                )
                self.assertEqual(expected_status, row["status"])

    def test_core_endpoint_is_cached_and_does_not_collect_diagnostics_or_ops(self):
        snapshot = core_snapshot()
        with (
            mock.patch.object(monitor, "collect_core_snapshot", return_value=snapshot) as collect_core,
            mock.patch.object(monitor, "get_server_rows") as servers,
            mock.patch.object(monitor, "get_scrape_targets") as targets,
            mock.patch.object(monitor, "get_prometheus_alerts") as alerts,
            mock.patch.object(monitor, "get_ops_health") as ops_health,
            mock.patch.object(monitor, "get_ops_crawler_summary") as ops_crawler,
            mock.patch.object(monitor, "get_ops_quality_summary") as ops_quality,
        ):
            first = self.client.get(
                "/api/monitoring/core",
                headers=self.auth_headers,
            )
            second = self.client.get(
                "/api/core",
                headers=self.auth_headers,
            )

        self.assertEqual(200, first.status_code)
        self.assertEqual(first.get_json(), second.get_json())
        self.assertEqual(list(monitor.CORE_SERVICE_ORDER), [
            row["service"] for row in first.get_json()["core_services"]
        ])
        self.assertEqual(
            "cloud",
            first.get_json()["topology"]["service_nodes"]["crawler"],
        )
        self.assertEqual("cloud", first.get_json()["topology"]["crawler_runtime_node"])
        self.assertEqual(
            "gen1crawler",
            first.get_json()["topology"]["crawler_target_node"],
        )
        self.assertEqual(
            "gen1db",
            first.get_json()["topology"]["crawler_control_node"],
        )
        self.assertEqual("legacy", first.get_json()["topology"]["crawler_mode"])
        self.assertEqual(
            "cutover_pending",
            first.get_json()["topology"]["crawler_transition_state"],
        )
        self.assertTrue(first.get_json()["topology"]["crawler_runtime_drift"])
        self.assertNotIn("servers", first.get_json())
        self.assertNotIn("targets", first.get_json())
        self.assertNotIn("alerts", first.get_json())
        collect_core.assert_called_once_with()
        for collector in (servers, targets, alerts, ops_health, ops_crawler, ops_quality):
            collector.assert_not_called()

    def test_primary_status_uses_writable_and_exported_role_evidence(self):
        def query_result(query):
            if query == "mooncen_postgres_in_recovery":
                return [
                    {"metric": {"node": "cloud"}, "value": [1, "0"]},
                    {"metric": {"node": "gen1db"}, "value": [1, "0"]},
                ]
            return [{
                "metric": {"node": "cloud", "role": "legacy", "exported_role": "primary"},
                "value": [1, "1"],
            }]

        with mock.patch.object(monitor, "query_vector", side_effect=query_result):
            primary = monitor.get_primary_status(monitor.get_core_topology())

        self.assertEqual("healthy", primary["status"])
        self.assertEqual("cloud", primary["node"])
        self.assertEqual(["cloud"], primary["candidates"])
        self.assertTrue(primary["database_writable"])
        self.assertTrue(primary["role_ok"])

        with mock.patch.object(
            monitor,
            "query_vector",
            side_effect=lambda query: (
                [{"metric": {"node": "cloud"}, "value": [1, "1"]}]
                if query == "mooncen_postgres_in_recovery"
                else [{
                    "metric": {"node": "cloud", "exported_role": "primary"},
                    "value": [1, "1"],
                }]
            ),
        ):
            standby = monitor.get_primary_status(monitor.get_core_topology())
        self.assertEqual("critical", standby["status"])
        self.assertFalse(standby["database_writable"])

        with mock.patch.object(
            monitor,
            "query_vector",
            side_effect=lambda query: (
                [
                    {"metric": {"node": "cloud"}, "value": [1, "0"]},
                    {"metric": {"node": "n100"}, "value": [1, "0"]},
                ]
                if query == "mooncen_postgres_in_recovery"
                else [
                    {
                        "metric": {"node": "cloud", "exported_role": "primary"},
                        "value": [1, "1"],
                    },
                    {
                        "metric": {"node": "n100", "exported_role": "primary"},
                        "value": [1, "1"],
                    },
                ]
            ),
        ):
            split_brain = monitor.get_primary_status(monitor.get_core_topology())
        self.assertEqual("critical", split_brain["status"])
        self.assertEqual(["cloud", "n100"], split_brain["candidates"])
        self.assertIsNone(split_brain["node"])

    def test_primary_status_is_unknown_when_prometheus_evidence_is_missing(self):
        with mock.patch.object(monitor, "query_vector", return_value=[]):
            primary = monitor.get_primary_status(monitor.get_core_topology())
        self.assertEqual("unknown", primary["status"])
        self.assertIsNone(primary["database_writable"])
        self.assertEqual([], primary["candidates"])

    def test_functional_probes_validate_real_response_contracts(self):
        frontend = mock.Mock(status_code=200, text='<html><div id="root"></div></html>')
        health = mock.Mock(status_code=200)
        health.json.return_value = {"status": "ready"}
        courses = mock.Mock(status_code=200)
        courses.json.return_value = {"total": 1, "items": [{"id": "course-1"}]}

        with mock.patch.object(
            monitor,
            "public_service_get",
            side_effect=lambda path: {
                "/": frontend,
                "/health": health,
                "/api/courses/?size=1": courses,
            }[path],
        ):
            self.assertTrue(monitor.probe_frontend()["ok"])
            self.assertTrue(monitor.probe_public_health()["ok"])
            self.assertTrue(monitor.probe_course_list()["ok"])

        frontend.text = "<html>wrong app</html>"
        health.status_code = 503
        courses.status_code = 200
        courses.json.return_value = {"total": 0, "items": []}
        with mock.patch.object(
            monitor,
            "public_service_get",
            side_effect=lambda path: {
                "/": frontend,
                "/health": health,
                "/api/courses/?size=1": courses,
            }[path],
        ):
            self.assertFalse(monitor.probe_frontend()["ok"])
            self.assertFalse(monitor.probe_public_health()["ok"])
            self.assertFalse(monitor.probe_course_list()["ok"])

    def test_public_service_get_retries_transport_errors_and_5xx_once(self):
        healthy = mock.Mock(status_code=200)
        with mock.patch.object(
            monitor.requests,
            "get",
            side_effect=[monitor.requests.ConnectionError("offline"), healthy],
        ) as request_get:
            self.assertIs(healthy, monitor.public_service_get("/health"))
        self.assertEqual(2, request_get.call_count)

        unavailable = mock.Mock(status_code=503)
        with mock.patch.object(
            monitor.requests,
            "get",
            side_effect=[unavailable, healthy],
        ) as request_get:
            self.assertIs(healthy, monitor.public_service_get("/health"))
        self.assertEqual(2, request_get.call_count)

    def test_crawler_functional_status_requires_fresh_success_evidence(self):
        now = 2_000_000.0

        def metric_value(query):
            if "state_valid" in query:
                return 1
            if "outcome" in query:
                return 0
            if "providers_requested" in query:
                return 5
            if "providers_failed" in query:
                return 0
            if "last_completion" in query:
                return now - 60
            if "unit_result_failed" in query:
                return 0
            return None

        with mock.patch.object(monitor, "nullable_first_value", side_effect=metric_value):
            healthy = monitor.get_crawler_functional_status("cloud", now=now)
        self.assertTrue(healthy["ok"])
        self.assertEqual("healthy", healthy["status"])

        def failed_metric(query):
            if "outcome" in query:
                return 1
            return metric_value(query)

        with mock.patch.object(monitor, "nullable_first_value", side_effect=failed_metric):
            degraded = monitor.get_crawler_functional_status("cloud", now=now)
        self.assertFalse(degraded["ok"])
        self.assertEqual("warning", degraded["status"])

        with mock.patch.object(
            monitor,
            "nullable_first_value",
            side_effect=lambda query: (
                now - monitor.CORE_CRAWLER_MAX_AGE_SECONDS - 1
                if "last_completion" in query
                else metric_value(query)
            ),
        ):
            stale = monitor.get_crawler_functional_status("cloud", now=now)
        self.assertFalse(stale["ok"])
        self.assertEqual("critical", stale["status"])

        with mock.patch.object(monitor, "nullable_first_value", return_value=None):
            unknown = monitor.get_crawler_functional_status("cloud", now=now)
        self.assertIsNone(unknown["ok"])
        self.assertEqual("unknown", unknown["status"])

    def test_crawler_functional_status_uses_existing_last_success_metric_as_fallback(self):
        now = 2_000_000.0

        def fallback_metric(query):
            if "crawler_last_success_timestamp" in query:
                return now - 60
            if "unit_result_failed" in query:
                return 0
            return None

        with mock.patch.object(monitor, "nullable_first_value", side_effect=fallback_metric):
            healthy = monitor.get_crawler_functional_status("cloud", now=now)
        self.assertTrue(healthy["ok"])
        self.assertEqual("healthy", healthy["status"])

        def failed_metric(query):
            if "unit_result_failed" in query:
                return 1
            return fallback_metric(query)

        with mock.patch.object(monitor, "nullable_first_value", side_effect=failed_metric):
            warning = monitor.get_crawler_functional_status("cloud", now=now)
        self.assertFalse(warning["ok"])
        self.assertEqual("warning", warning["status"])

        with mock.patch.object(
            monitor,
            "nullable_first_value",
            side_effect=lambda query: (
                now - monitor.CORE_CRAWLER_MAX_AGE_SECONDS - 1
                if "crawler_last_success_timestamp" in query
                else fallback_metric(query)
            ),
        ):
            stale = monitor.get_crawler_functional_status("cloud", now=now)
        self.assertFalse(stale["ok"])
        self.assertEqual("critical", stale["status"])

    def test_crawler_warns_when_timer_trigger_is_newer_than_completion(self):
        now = 2_000_000.0

        def detailed_metric(query, *, active=0, trigger_age=600):
            if "state_valid" in query:
                return 1
            if "outcome" in query:
                return 0
            if "providers_requested" in query:
                return 5
            if "providers_failed" in query:
                return 0
            if "cycle_last_completion" in query:
                return now - 3600
            if "timer_last_trigger" in query:
                return now - trigger_age
            if "crawler-once.service" in query and "unit_state" in query:
                return active
            if "unit_result_failed" in query:
                return 0
            return None

        with mock.patch.object(
            monitor,
            "nullable_first_value",
            side_effect=detailed_metric,
        ):
            warning = monitor.get_crawler_functional_status("cloud", now=now)
        self.assertFalse(warning["ok"])
        self.assertEqual("warning", warning["status"])

        for active, trigger_age in ((1, 600), (0, 299), (0, 7200)):
            with self.subTest(active=active, trigger_age=trigger_age):
                with mock.patch.object(
                    monitor,
                    "nullable_first_value",
                    side_effect=lambda query, active=active, trigger_age=trigger_age: (
                        detailed_metric(query, active=active, trigger_age=trigger_age)
                    ),
                ):
                    status = monitor.get_crawler_functional_status("cloud", now=now)
                self.assertTrue(status["ok"])
                self.assertEqual("healthy", status["status"])

    def test_crawler_timer_completion_check_also_applies_to_legacy_fallback(self):
        now = 2_000_000.0

        def fallback_metric(query, *, active=0, trigger_age=600):
            if "crawler_last_success_timestamp" in query:
                return now - 3600
            if "timer_last_trigger" in query:
                return now - trigger_age
            if "crawler-once.service" in query and "unit_state" in query:
                return active
            if "unit_result_failed" in query:
                return 0
            return None

        with mock.patch.object(
            monitor,
            "nullable_first_value",
            side_effect=fallback_metric,
        ):
            warning = monitor.get_crawler_functional_status("cloud", now=now)
        self.assertFalse(warning["ok"])
        self.assertEqual("warning", warning["status"])

        for active, trigger_age in ((1, 600), (0, 299), (0, 7200)):
            with self.subTest(active=active, trigger_age=trigger_age):
                with mock.patch.object(
                    monitor,
                    "nullable_first_value",
                    side_effect=lambda query, active=active, trigger_age=trigger_age: (
                        fallback_metric(query, active=active, trigger_age=trigger_age)
                    ),
                ):
                    status = monitor.get_crawler_functional_status("cloud", now=now)
                self.assertTrue(status["ok"])
                self.assertEqual("healthy", status["status"])

    def test_collect_core_snapshot_preserves_nullable_runtime_and_fixed_order(self):
        runtime = {
            service: {"runtime_ok": None, "active_nodes": [], "value": None}
            for service in monitor.CORE_SERVICE_ORDER
        }
        primary = core_snapshot()["primary"]
        with (
            mock.patch.object(monitor, "get_core_runtime_status", return_value=runtime),
            mock.patch.object(monitor, "get_primary_status", return_value=primary),
            mock.patch.object(monitor, "probe_frontend", return_value={"ok": True, "detail": "ok"}),
            mock.patch.object(monitor, "probe_public_health", return_value={"ok": True, "detail": "ok"}),
            mock.patch.object(monitor, "probe_course_list", return_value={"ok": True, "detail": "ok"}),
            mock.patch.object(
                monitor,
                "get_crawler_functional_status",
                return_value={"ok": True, "detail": "ok"},
            ),
        ):
            snapshot = monitor.collect_core_snapshot()

        self.assertEqual("warning", snapshot["status"])
        self.assertEqual(list(monitor.CORE_SERVICE_ORDER), [
            row["service"] for row in snapshot["core_services"]
        ])
        self.assertTrue(all(row["runtime_ok"] is None for row in snapshot["core_services"]))
        self.assertTrue(all(row["functional_ok"] is True for row in snapshot["core_services"]))
        self.assertTrue(all(row["status"] == "warning" for row in snapshot["core_services"]))
        self.assertEqual([], snapshot["problems"])

    def test_collect_core_snapshot_treats_explicit_crawler_timer_down_as_critical(self):
        runtime = {
            service: {"runtime_ok": True, "active_nodes": ["cloud"], "value": 1}
            for service in monitor.CORE_SERVICE_ORDER
        }
        runtime["crawler"] = {"runtime_ok": False, "active_nodes": [], "value": 0}
        primary = core_snapshot()["primary"]
        with (
            mock.patch.object(monitor, "get_core_runtime_status", return_value=runtime),
            mock.patch.object(monitor, "get_primary_status", return_value=primary),
            mock.patch.object(monitor, "probe_frontend", return_value={"ok": True, "detail": "ok"}),
            mock.patch.object(monitor, "probe_public_health", return_value={"ok": True, "detail": "ok"}),
            mock.patch.object(monitor, "probe_course_list", return_value={"ok": True, "detail": "ok"}),
            mock.patch.object(
                monitor,
                "get_crawler_functional_status",
                return_value={"ok": True, "status": "healthy", "detail": "ok"},
            ),
        ):
            snapshot = monitor.collect_core_snapshot()

        crawler = snapshot["core_services"][-1]
        self.assertEqual("crawler", crawler["service"])
        self.assertFalse(crawler["runtime_ok"])
        self.assertEqual("critical", crawler["status"])
        self.assertEqual(["service:crawler"], [item["key"] for item in snapshot["problems"]])

    def test_monitoring_trends_alias_and_metrics_endpoint(self):
        with mock.patch.object(
            monitor,
            "query_range",
            return_value=[],
        ) as query_range:
            response = self.client.get(
                "/api/monitoring/trends?hours=1",
                headers=self.auth_headers,
            )
        self.assertEqual(200, response.status_code)
        self.assertEqual({"cpu", "mem", "temp"}, set(response.get_json()["series"]))
        queried_promql = [call.args[0] for call in query_range.call_args_list]
        self.assertTrue(
            any("node_thermal_temperature_celsius" in query for query in queried_promql)
        )
        self.assertTrue(
            any("node_memory_inactive_bytes" in query for query in queried_promql)
        )

        response = self.client.get(
            "/api/monitoring/trends?hours=0",
            headers=self.auth_headers,
        )
        self.assertEqual(400, response.status_code)

        response = self.client.get(
            "/api/monitoring/trends?hours=not-a-number",
            headers=self.auth_headers,
        )
        self.assertEqual(400, response.status_code)

        with mock.patch.object(
            monitor,
            "build_ops_metrics",
            return_value=["# TYPE mooncen_test gauge", "mooncen_test 1"],
        ):
            response = self.client.get("/metrics")
        self.assertEqual(200, response.status_code)
        self.assertIn("mooncen_test 1", response.get_data(as_text=True))

    def test_primary_metric_is_one_hot_across_all_status_values(self):
        snapshot = core_snapshot(primary_status="unknown")
        with (
            mock.patch.object(monitor, "build_scrape_target_metrics", return_value=[]),
            mock.patch.object(monitor, "build_node_summary_metrics", return_value=[]),
            mock.patch.object(monitor, "build_service_metrics", return_value=[]),
            mock.patch.object(monitor, "get_cached_core_snapshot", return_value=snapshot),
        ):
            lines = monitor.build_ops_metrics()

        primary_lines = [line for line in lines if line.startswith("mooncen_primary_status{")]
        self.assertEqual(4, len(primary_lines))
        self.assertEqual(1, sum(line.endswith(" 1.0") for line in primary_lines))
        self.assertTrue(any('status="unknown"' in line and line.endswith(" 1.0") for line in primary_lines))

    def test_operation_endpoint_rejects_unknown_action(self):
        with (
            mock.patch.object(monitor, "OPERATION_ENABLED", True),
            mock.patch.object(monitor, "OPERATION_TOKEN", "operation-secret"),
        ):
            response = self.client.post(
                "/api/operation/run",
                json={"action": "arbitrary-shell-command"},
                headers={
                    "X-App-Token": "test-secret",
                    "X-Operation-Token": "operation-secret",
                },
            )
        self.assertEqual(400, response.status_code)
        self.assertEqual("unknown action", response.get_json()["error"])

    def test_read_token_cannot_run_operation(self):
        with (
            mock.patch.object(monitor, "OPERATION_ENABLED", True),
            mock.patch.object(monitor, "OPERATION_TOKEN", "operation-secret"),
        ):
            response = self.client.post(
                "/api/operation/run",
                json={"action": "restart_cloud_frontend"},
                headers=self.auth_headers,
            )
        self.assertEqual(401, response.status_code)

    def test_healthz_is_minimal_and_does_not_require_token(self):
        response = self.client.get("/healthz")
        self.assertEqual(200, response.status_code)
        self.assertEqual({"ok": True}, response.get_json())

    def test_tailscale_snapshot_is_re_allowlisted_and_marked_stale(self):
        raw = {
            "schema_version": 1,
            "generated_at": "2026-07-29T00:00:00Z",
            "backend_state": "Running",
            "NodeKey": "nodekey:top-secret",
            "counts": {"total": 999, "online": 999, "offline": 0},
            "self": {
                "name": "mooncen",
                "dns_name": "cloud",
                "os": "linux",
                "online": True,
                "active": True,
                "connection": "direct",
                "last_seen": "2026-07-28T23:59:00Z",
                "key_expiry": "2026-12-01T00:00:00Z",
                "MachineKey": "mkey:self-secret",
                "Endpoints": ["203.0.113.1:41641"],
            },
            "peers": [
                {
                    "name": "gen1db",
                    "dns_name": "gen1db",
                    "os": "linux",
                    "online": True,
                    "active": False,
                    "connection": "direct",
                    "last_seen": "2026-07-28T23:58:00Z",
                    "key_expiry": None,
                    "UserID": 1234,
                    "CurAddr": "198.51.100.2:41641",
                },
                {
                    "name": "old-nas",
                    "dns_name": "ds1515",
                    "os": "linux",
                    "online": True,
                    "active": True,
                    "connection": "relay",
                },
            ],
        }

        status = monitor.normalize_tailscale_snapshot(
            raw,
            now=datetime(2026, 7, 29, 0, 4, tzinfo=timezone.utc),
            max_age_seconds=180,
        )
        encoded = json.dumps(status)

        self.assertTrue(status["available"])
        self.assertTrue(status["stale"])
        self.assertEqual("stale", status["status"])
        self.assertEqual({"total": 1, "online": 1, "offline": 0}, status["counts"])
        self.assertEqual(
            {"total": 1, "online": 1, "offline": 0, "direct": 0, "relay": 0},
            status["summary"],
        )
        self.assertEqual("cloud", status["self"]["dns_name"])
        self.assertEqual("idle", status["peers"][0]["connection"])
        self.assertEqual(["gen1db"], [peer["name"] for peer in status["peers"]])
        for secret in (
            "nodekey:",
            "mkey:",
            "203.0.113.1",
            "198.51.100.2",
            "NodeKey",
            "MachineKey",
            "Endpoints",
            "UserID",
            "CurAddr",
        ):
            self.assertNotIn(secret, encoded)

    def test_tailscale_reader_and_endpoints_handle_current_snapshot(self):
        generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        snapshot = {
            "schema_version": 1,
            "generated_at": generated_at,
            "backend_state": "Running",
            "counts": {"total": 1, "online": 1, "offline": 0},
            "self": None,
            "peers": [{
                "name": "cloud",
                "dns_name": "cloud",
                "os": "linux",
                "online": True,
                "active": True,
                "connection": "relay",
                "last_seen": generated_at,
                "key_expiry": None,
            }],
        }
        with mock.patch.object(
            monitor,
            "read_tailscale_snapshot_file",
            return_value=snapshot,
        ):
            response = self.client.get(
                "/api/monitoring/tailscale",
                headers=self.auth_headers,
            )
            with mock.patch.object(monitor, "get_server_rows", return_value=[]):
                servers_response = self.client.get(
                    "/api/monitoring/servers",
                    headers=self.auth_headers,
                )

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["available"])
        self.assertEqual("current", response.get_json()["status"])
        self.assertEqual(response.get_json(), servers_response.get_json()["tailscale"])

    def test_tailscale_reader_reports_generic_missing_error(self):
        with mock.patch.object(
            monitor,
            "TAILSCALE_STATUS_FILE",
            os.path.abspath(os.path.join("does-not-exist", "tailscale-status.json")),
        ):
            status = monitor.get_tailscale_status()
        self.assertEqual("snapshot_missing", status["error"])
        self.assertFalse(status["available"])
        self.assertNotIn("does-not-exist", json.dumps(status))

    def test_tailscale_reader_rejects_non_root_owner_on_posix(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = Path(directory, "tailscale-status.json")
            snapshot_path.write_text("{}", encoding="utf-8")
            metadata = mock.Mock(
                st_mode=monitor.stat.S_IFREG | 0o640,
                st_size=2,
                st_uid=1000,
            )
            with (
                mock.patch.object(monitor, "POSIX_SNAPSHOT_SECURITY", True),
                mock.patch.object(monitor.os, "fstat", return_value=metadata),
            ):
                with self.assertRaises(PermissionError):
                    monitor.read_tailscale_snapshot_file(str(snapshot_path.resolve()))

    def test_primary_problem_is_stable_and_only_emitted_when_critical(self):
        critical = core_snapshot(primary_status="critical")
        self.assertEqual(
            ["primary:database"],
            [item["key"] for item in critical["problems"]],
        )

        unknown = core_snapshot(primary_status="unknown")
        self.assertEqual([], unknown["problems"])


if __name__ == "__main__":
    unittest.main()
