from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from ops_agent.production_topology import (
    PRODUCTION_TOPOLOGY_PATH,
    load_production_topology,
    production_service_placements,
    public_production_topology,
)


ROOT = Path(__file__).resolve().parents[1]


def _manifest() -> dict[str, object]:
    return json.loads((ROOT / PRODUCTION_TOPOLOGY_PATH).read_text(encoding="utf-8"))


def _write_manifest(root: Path, payload: dict[str, object]) -> None:
    path = root / PRODUCTION_TOPOLOGY_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_active_cloud_owns_the_production_web_backend_and_database() -> None:
    topology = load_production_topology(ROOT)

    assert topology.active_node == "cloud"
    assert topology.primary_for("frontend").service_host == "cloud"
    assert topology.primary_for("backend").service_host == "cloud"
    assert topology.primary_for("database").service_host == "cloud"


def test_cloud_owns_database_and_gen1crawler_owns_legacy_crawling() -> None:
    database = production_service_placements("database", ROOT)
    crawler = production_service_placements("crawler", ROOT)

    assert [(placement.node, placement.role) for placement in database] == [("cloud", "primary")]
    assert [(placement.node, placement.role) for placement in crawler] == [
        ("gen1crawler", "primary")
    ]
    assert load_production_topology(ROOT).primary_for("crawler").service_host == "gen1crawler"


def test_gen1db_owns_crawler_control_and_isolated_staging_database() -> None:
    topology = load_production_topology(ROOT)
    production_database = topology.primary_for("database")
    staging_database = topology.primary_for("staging_database")
    crawler_control = topology.primary_for("crawler_control")

    assert (production_database.node, production_database.service_host) == (
        "cloud",
        "cloud",
    )
    assert (staging_database.node, staging_database.service_host) == (
        "gen1db",
        "gen1db",
    )
    assert (crawler_control.node, crawler_control.service_host) == (
        "gen1db",
        "gen1db",
    )
    assert staging_database.node != production_database.node


def test_production_crawler_mode_remains_explicitly_legacy_until_cutover() -> None:
    topology = load_production_topology(ROOT)

    assert topology.crawler_mode == "legacy"
    assert topology.public_payload()["crawler_mode"] == "legacy"


def test_reviewed_distributed_worker_fleet_is_explicit_and_disabled() -> None:
    topology = load_production_topology(ROOT)

    assert [
        worker.worker_key
        for worker in sorted(
            topology.crawler_workers.values(),
            key=lambda item: item.rollout_order,
        )
    ] == ["wtr-linux", "gen1crawler"]
    wtr = topology.crawler_worker_for("wtr-linux")
    assert wtr.topology_node == "wtr-linux"
    assert wtr.dns_host == "wtr-linux"
    assert wtr.kernel_hostname == "sgm-standard-pc-i440fx-piix-1996"
    assert (wtr.canary, wtr.rollout_order, wtr.enabled) == (True, 1, False)
    assert (wtr.concurrency, wtr.memory_high, wtr.memory_max, wtr.cpu_quota) == (
        1,
        "4G",
        "6G",
        "300%",
    )
    gen1crawler = topology.crawler_worker_for("gen1crawler")
    assert gen1crawler.kernel_hostname == "gen1crawler"
    assert (gen1crawler.canary, gen1crawler.rollout_order, gen1crawler.enabled) == (
        False,
        2,
        False,
    )
    assert (
        gen1crawler.concurrency,
        gen1crawler.memory_high,
        gen1crawler.memory_max,
        gen1crawler.cpu_quota,
    ) == (1, "2G", "4G", "200%")


def test_public_payload_contains_only_dns_names_and_reviewed_placement_data() -> None:
    payload = public_production_topology(ROOT)
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["active_node"] == "cloud"
    assert payload["crawler_mode"] == "legacy"
    assert payload["nodes"]["cloud"] == {"dns_host": "cloud", "active": True}
    assert payload["nodes"]["gen1crawler"] == {
        "dns_host": "gen1crawler",
        "active": False,
    }
    assert payload["nodes"]["gen1db"] == {
        "dns_host": "gen1db",
        "active": False,
    }
    assert payload["nodes"]["wtr-linux"] == {
        "dns_host": "wtr-linux",
        "active": False,
    }
    assert set(payload["nodes"]) == {
        "cloud",
        "gen1crawler",
        "gen1db",
        "wtr-linux",
    }
    assert [worker["worker_key"] for worker in payload["crawler_workers"]] == [
        "wtr-linux",
        "gen1crawler",
    ]
    assert all(worker["enabled"] is False for worker in payload["crawler_workers"])
    assert not re.search(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)", encoded)
    for forbidden in ("password", "secret", "token", "api_key", "identity_file"):
        assert forbidden not in encoded.lower()


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (
            lambda payload: payload["nodes"]["cloud"].update({"password": "not-public"}),
            "unsupported field",
        ),
        (
            lambda payload: payload["nodes"]["cloud"].update({"dnsHost": "127.0.0.1"}),
            "IP literal",
        ),
        (
            lambda payload: (
                payload["nodes"].update({"backup": {"dnsHost": "backup"}}),
                payload["services"]["database"].append(
                    {
                        "node": "backup",
                        "role": "standby",
                        "replicatesFrom": "backup",
                    }
                ),
            ),
            "standby source",
        ),
        (
            lambda payload: payload.update({"crawlerMode": "hybrid"}),
            "crawlerMode must be legacy or distributed",
        ),
        (
            lambda payload: payload.pop("crawlerMode"),
            "missing a required field",
        ),
        (
            lambda payload: payload["crawlerWorkers"][0].update({"enabled": True}),
            "legacy mode forbids enabled crawler workers",
        ),
        (
            lambda payload: payload["crawlerWorkers"][0].update(
                {"dnsHost": "gen1crawler"}
            ),
            "dnsHost differs from its node",
        ),
        (
            lambda payload: payload["crawlerWorkers"][1].update(
                {"rolloutOrder": 1}
            ),
            "identity or order is duplicated",
        ),
        (
            lambda payload: payload["crawlerWorkers"][0].update(
                {"kernelHostname": "WTR-LINUX"}
            ),
            "dnsHost is invalid",
        ),
        (
            lambda payload: payload["crawlerWorkers"][0]["resourceLimits"].update(
                {"memoryHigh": "6G"}
            ),
            "MemoryHigh must be below MemoryMax",
        ),
        (
            lambda payload: payload["services"].pop("staging_database"),
            "missing a required service",
        ),
        (
            lambda payload: payload["services"]["crawler_control"][0].update(
                {"node": "cloud"}
            ),
            "crawler_control primary must not be on activeNode",
        ),
        (
            lambda payload: payload["services"]["staging_database"][0].update(
                {"node": "cloud"}
            ),
            "staging_database primary must not be on activeNode",
        ),
        (
            lambda payload: (
                payload["nodes"].update(
                    {"gen1staging": {"dnsHost": "gen1staging"}}
                ),
                payload["services"]["staging_database"][0].update(
                    {"node": "gen1staging"}
                ),
            ),
            "staging_database primary must be co-located with crawler_control primary",
        ),
    ],
)
def test_loader_rejects_unsafe_or_inconsistent_topology(
    tmp_path: Path,
    mutate,
    expected: str,
) -> None:
    payload = copy.deepcopy(_manifest())
    mutate(payload)
    _write_manifest(tmp_path, payload)

    with pytest.raises(ValueError, match=expected):
        load_production_topology(tmp_path)


def test_distributed_worker_rollout_cannot_skip_the_canary(
    tmp_path: Path,
) -> None:
    payload = copy.deepcopy(_manifest())
    payload["crawlerMode"] = "distributed"
    payload["crawlerWorkers"][1]["enabled"] = True
    _write_manifest(tmp_path, payload)

    with pytest.raises(ValueError, match="canary-first contiguous rollout prefix"):
        load_production_topology(tmp_path)
