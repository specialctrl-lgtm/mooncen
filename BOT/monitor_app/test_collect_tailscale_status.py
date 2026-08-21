import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from monitor_app import collect_tailscale_status as collector


class TailscaleStatusCollectorTest(unittest.TestCase):
    def test_excluded_nodes_environment_adds_to_defaults(self):
        with mock.patch.dict(
            os.environ,
            {"MONITOR_APP_EXCLUDED_NODES": " custom, NAS.EXAMPLE "},
        ):
            self.assertEqual(
                {"ds1515", "ds718", "n100", "custom", "nas.example"},
                collector.excluded_node_names(),
            )

    def test_sanitizer_uses_allowlist_and_drops_excluded_nodes(self):
        raw = {
            "BackendState": "Running",
            "MagicDNSSuffix": "secret-tailnet.ts.net",
            "Self": {
                "HostName": "bot",
                "DNSName": "monitor.secret-tailnet.ts.net.",
                "OS": "linux",
                "Online": True,
                "Active": True,
                "CurAddr": "203.0.113.1:41641",
                "LastSeen": "2026-07-29T01:02:03Z",
                "KeyExpiry": "2026-12-01T00:00:00Z",
                "NodeKey": "nodekey:self-secret",
                "MachineKey": "mkey:self-secret",
                "UserID": 1234,
                "TailscaleIPs": ["100.64.0.1"],
                "Endpoints": ["203.0.113.1:41641"],
            },
            "Peer": {
                "nodekey:peer-secret": {
                    "HostName": "mooncen",
                    "DNSName": "cloud.secret-tailnet.ts.net.",
                    "OS": "linux",
                    "Online": True,
                    "Active": False,
                    "CurAddr": "198.51.100.4:41641",
                    "Relay": "sel",
                    "LastSeen": "2026-07-29T01:00:00Z",
                    "NodeKey": "nodekey:duplicate-secret",
                    "MachineKey": "mkey:peer-secret",
                    "UserID": 5678,
                    "Endpoints": ["198.51.100.4:41641"],
                    "Nested": {"NodeKey": "must-not-survive"},
                },
                "nodekey:excluded-secret": {
                    "HostName": "ds1515",
                    "OS": "linux",
                    "Online": True,
                },
                "nodekey:n100-secret": {
                    "HostName": "n100",
                    "OS": "linux",
                    "Online": True,
                },
                "nodekey:victus-secret": {
                    "HostName": "victus",
                    "OS": "windows",
                    "Online": True,
                },
            },
        }

        snapshot = collector.sanitize_status(
            raw,
            generated_at="2026-07-29T02:00:00Z",
        )
        encoded = json.dumps(snapshot)

        self.assertEqual(1, snapshot["schema_version"])
        self.assertEqual({"total": 2, "online": 2, "offline": 0}, snapshot["counts"])
        self.assertEqual("bot", snapshot["self"]["name"])
        self.assertEqual("monitor", snapshot["self"]["dns_name"])
        self.assertEqual("direct", snapshot["self"]["connection"])
        self.assertEqual(
            ["mooncen", "victus"],
            [peer["name"] for peer in snapshot["peers"]],
        )
        self.assertEqual("cloud", snapshot["peers"][0]["dns_name"])
        self.assertEqual("idle", snapshot["peers"][0]["connection"])
        for secret in (
            "nodekey:",
            "mkey:",
            "secret-tailnet",
            "100.64.0.1",
            "203.0.113.1",
            "198.51.100.4",
            "UserID",
            "Endpoints",
            "MachineKey",
            "NodeKey",
            "CurAddr",
            "Relay",
            "203.0.113.1:41641",
            "198.51.100.4:41641",
        ):
            self.assertNotIn(secret, encoded)

    def test_connection_is_derived_without_copying_address_or_relay(self):
        cases = (
            ({"Online": False, "Active": True, "CurAddr": "secret"}, "offline"),
            ({"Online": True, "Active": False, "CurAddr": "secret"}, "idle"),
            ({"Online": True, "Active": True, "CurAddr": "secret"}, "direct"),
            ({"Online": True, "Active": True, "Relay": "sel"}, "relay"),
            ({"Online": True, "Active": True}, "unknown"),
        )
        for fields, expected in cases:
            with self.subTest(expected=expected):
                node = collector.sanitize_node({"HostName": "node", **fields})
                self.assertEqual(expected, node["connection"])
                self.assertNotIn("CurAddr", node)
                self.assertNotIn("Relay", node)

    def test_atomic_writer_creates_private_group_readable_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory, "tailscale-status.json")
            collector.atomic_write_snapshot(
                output,
                collector.sanitize_status({}, generated_at="2026-07-29T02:00:00Z"),
            )

            if os.name != "nt":
                self.assertEqual(0o640, stat.S_IMODE(output.stat().st_mode))
            self.assertEqual(1, json.loads(output.read_text())["schema_version"])
            self.assertEqual([], list(output.parent.glob(f".{output.name}.*")))


if __name__ == "__main__":
    unittest.main()
