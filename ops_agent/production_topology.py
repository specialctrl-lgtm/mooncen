"""Validated, public-only description of the desired production placement."""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_TOPOLOGY_PATH = Path("config/production_topology.json")

_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_NODES = 16
_MAX_SERVICES = 32
_MAX_PLACEMENTS_PER_SERVICE = 16
_MAX_CRAWLER_WORKERS = 16
_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_WORKER_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_DNS_HOST_PATTERN = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)
_MEMORY_LIMIT_PATTERN = re.compile(r"^([1-9][0-9]{0,3})([MG])$")
_CPU_QUOTA_PATTERN = re.compile(r"^([1-9][0-9]{0,2})%$")
_REQUIRED_SERVICES = frozenset(
    {
        "frontend",
        "backend",
        "database",
        "staging_database",
        "crawler",
        "crawler_control",
    }
)
_ACTIVE_NODE_SERVICES = frozenset({"frontend", "backend", "database"})
_CONTROL_PLANE_SERVICE = "crawler_control"
_STAGING_DATABASE_SERVICE = "staging_database"
_CRAWLER_MODES = frozenset({"legacy", "distributed"})


@dataclass(frozen=True)
class ProductionNode:
    name: str
    dns_host: str


@dataclass(frozen=True)
class CrawlerWorker:
    worker_key: str
    topology_node: str
    dns_host: str
    kernel_hostname: str
    canary: bool
    rollout_order: int
    enabled: bool
    concurrency: int
    memory_high: str
    memory_max: str
    cpu_quota: str

    def public_dict(self) -> dict[str, str | int | bool]:
        return {
            "worker_key": self.worker_key,
            "topology_node": self.topology_node,
            "dns_host": self.dns_host,
            "kernel_hostname": self.kernel_hostname,
            "canary": self.canary,
            "rollout_order": self.rollout_order,
            "enabled": self.enabled,
            "concurrency": self.concurrency,
            "memory_high": self.memory_high,
            "memory_max": self.memory_max,
            "cpu_quota": self.cpu_quota,
        }


@dataclass(frozen=True)
class ServicePlacement:
    service: str
    node: str
    service_host: str
    role: str
    replicates_from: str | None = None

    def public_dict(self) -> dict[str, str | None]:
        return {
            "node": self.node,
            "service_host": self.service_host,
            "role": self.role,
            "replicates_from": self.replicates_from,
        }


@dataclass(frozen=True)
class ProductionTopology:
    schema_version: int
    environment: str
    active_node: str
    crawler_mode: str
    nodes: Mapping[str, ProductionNode]
    crawler_workers: Mapping[str, CrawlerWorker]
    services: Mapping[str, tuple[ServicePlacement, ...]]

    def placements_for(self, service: str) -> tuple[ServicePlacement, ...]:
        """Return all reviewed placements for a service, primary first."""
        normalized = str(service).strip().lower()
        try:
            return self.services[normalized]
        except KeyError as exc:
            raise KeyError(f"unknown production service: {normalized or '<empty>'}") from exc

    def primary_for(self, service: str) -> ServicePlacement:
        """Return the single validated primary placement for a service."""
        return next(
            placement
            for placement in self.placements_for(service)
            if placement.role == "primary"
        )

    def crawler_worker_for(self, worker_key: str) -> CrawlerWorker:
        """Return one reviewed worker assignment by its stable queue key."""
        normalized = str(worker_key).strip()
        try:
            return self.crawler_workers[normalized]
        except KeyError as exc:
            raise KeyError(
                f"unknown production crawler worker: {normalized or '<empty>'}"
            ) from exc

    def public_payload(self) -> dict[str, Any]:
        """Return the secret-free structure safe for an operator API response."""
        return {
            "schema_version": self.schema_version,
            "environment": self.environment,
            "active_node": self.active_node,
            "crawler_mode": self.crawler_mode,
            "nodes": {
                name: {
                    "dns_host": node.dns_host,
                    "active": name == self.active_node,
                }
                for name, node in self.nodes.items()
            },
            "crawler_workers": [
                worker.public_dict()
                for worker in sorted(
                    self.crawler_workers.values(),
                    key=lambda item: item.rollout_order,
                )
            ],
            "services": {
                service: [placement.public_dict() for placement in placements]
                for service, placements in self.services.items()
            },
        }


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("production topology contains a duplicate field")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("production topology contains a non-finite number")


def _require_object(
    value: Any,
    *,
    field: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"production topology {field} must be an object")
    keys = set(value)
    if keys - required - optional:
        raise ValueError(f"production topology {field} contains an unsupported field")
    if required - keys:
        raise ValueError(f"production topology {field} is missing a required field")
    return value


def _identifier(value: Any, *, field: str) -> str:
    if type(value) is not str or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"production topology {field} is invalid")
    return value


def _dns_host(value: Any) -> str:
    if type(value) is not str or not _DNS_HOST_PATTERN.fullmatch(value):
        raise ValueError("production topology dnsHost is invalid")
    try:
        ipaddress.ip_address(value.rstrip("."))
    except ValueError:
        return value
    raise ValueError("production topology dnsHost must not be an IP literal")


def _read_manifest(root: Path) -> dict[str, Any]:
    root = root.resolve()
    candidate = root / PRODUCTION_TOPOLOGY_PATH
    if candidate.is_symlink():
        raise ValueError("reviewed production topology is unavailable")
    path = candidate.resolve()
    expected_parent = (root / PRODUCTION_TOPOLOGY_PATH.parent).resolve()
    try:
        if path.parent != expected_parent or path.is_symlink() or not path.is_file():
            raise ValueError("reviewed production topology is unavailable")
        if path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise ValueError("production topology is too large")
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except ValueError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("production topology is invalid") from exc
    return _require_object(
        payload,
        field="manifest",
        required=frozenset(
            {
                "schemaVersion",
                "environment",
                "activeNode",
                "crawlerMode",
                "nodes",
                "crawlerWorkers",
                "services",
            }
        ),
    )


def load_production_topology(root: Path = PROJECT_ROOT) -> ProductionTopology:
    """Load and validate the reviewed desired production topology."""
    payload = _read_manifest(root)
    if type(payload["schemaVersion"]) is not int or payload["schemaVersion"] != 1:
        raise ValueError("production topology schemaVersion is unsupported")
    if payload["environment"] != "production":
        raise ValueError("production topology environment must be production")
    crawler_mode = payload["crawlerMode"]
    if type(crawler_mode) is not str or crawler_mode not in _CRAWLER_MODES:
        raise ValueError(
            "production topology crawlerMode must be legacy or distributed"
        )

    raw_nodes = payload["nodes"]
    if type(raw_nodes) is not dict or not 1 <= len(raw_nodes) <= _MAX_NODES:
        raise ValueError("production topology nodes are invalid")
    nodes: dict[str, ProductionNode] = {}
    for raw_name, raw_node in raw_nodes.items():
        name = _identifier(raw_name, field="node name")
        node = _require_object(
            raw_node,
            field=f"node {name}",
            required=frozenset({"dnsHost"}),
        )
        nodes[name] = ProductionNode(name=name, dns_host=_dns_host(node["dnsHost"]))

    active_node = _identifier(payload["activeNode"], field="activeNode")
    if active_node not in nodes:
        raise ValueError("production topology activeNode is not a reviewed node")

    raw_crawler_workers = payload["crawlerWorkers"]
    if (
        type(raw_crawler_workers) is not list
        or not 1 <= len(raw_crawler_workers) <= _MAX_CRAWLER_WORKERS
    ):
        raise ValueError("production topology crawlerWorkers are invalid")
    crawler_workers: dict[str, CrawlerWorker] = {}
    worker_nodes: set[str] = set()
    kernel_hostnames: set[str] = set()
    rollout_orders: set[int] = set()
    for raw_worker in raw_crawler_workers:
        worker = _require_object(
            raw_worker,
            field="crawler worker",
            required=frozenset(
                {
                    "workerKey",
                    "topologyNode",
                    "dnsHost",
                    "kernelHostname",
                    "canary",
                    "rolloutOrder",
                    "enabled",
                    "resourceLimits",
                }
            ),
        )
        worker_key = worker["workerKey"]
        if type(worker_key) is not str or not _WORKER_KEY_PATTERN.fullmatch(worker_key):
            raise ValueError("production topology crawler workerKey is invalid")
        topology_node = _identifier(
            worker["topologyNode"],
            field="crawler worker topologyNode",
        )
        if topology_node not in nodes:
            raise ValueError("production topology crawler worker references an unknown node")
        dns_host = _dns_host(worker["dnsHost"])
        if dns_host != nodes[topology_node].dns_host:
            raise ValueError("production topology crawler worker dnsHost differs from its node")
        kernel_hostname = _dns_host(worker["kernelHostname"])
        canary = worker["canary"]
        enabled = worker["enabled"]
        rollout_order = worker["rolloutOrder"]
        if type(canary) is not bool or type(enabled) is not bool:
            raise ValueError("production topology crawler worker flags must be booleans")
        if (
            type(rollout_order) is not int
            or not 1 <= rollout_order <= _MAX_CRAWLER_WORKERS
        ):
            raise ValueError("production topology crawler worker rolloutOrder is invalid")

        raw_limits = _require_object(
            worker["resourceLimits"],
            field=f"crawler worker {worker_key} resourceLimits",
            required=frozenset(
                {"concurrency", "memoryHigh", "memoryMax", "cpuQuota"}
            ),
        )
        concurrency = raw_limits["concurrency"]
        if type(concurrency) is not int or not 1 <= concurrency <= 5:
            raise ValueError("production topology crawler worker concurrency is invalid")
        memory_high = raw_limits["memoryHigh"]
        memory_max = raw_limits["memoryMax"]
        high_match = (
            _MEMORY_LIMIT_PATTERN.fullmatch(memory_high)
            if type(memory_high) is str
            else None
        )
        max_match = (
            _MEMORY_LIMIT_PATTERN.fullmatch(memory_max)
            if type(memory_max) is str
            else None
        )
        if high_match is None or max_match is None:
            raise ValueError("production topology crawler worker memory limits are invalid")

        def memory_bytes(match: re.Match[str]) -> int:
            multiplier = 1024**3 if match.group(2) == "G" else 1024**2
            return int(match.group(1)) * multiplier

        if memory_bytes(high_match) >= memory_bytes(max_match):
            raise ValueError("production topology crawler worker MemoryHigh must be below MemoryMax")
        cpu_quota = raw_limits["cpuQuota"]
        quota_match = (
            _CPU_QUOTA_PATTERN.fullmatch(cpu_quota)
            if type(cpu_quota) is str
            else None
        )
        if quota_match is None or not 10 <= int(quota_match.group(1)) <= 400:
            raise ValueError("production topology crawler worker cpuQuota is invalid")
        if (
            worker_key in crawler_workers
            or topology_node in worker_nodes
            or kernel_hostname in kernel_hostnames
            or rollout_order in rollout_orders
        ):
            raise ValueError("production topology crawler worker identity or order is duplicated")
        crawler_workers[worker_key] = CrawlerWorker(
            worker_key=worker_key,
            topology_node=topology_node,
            dns_host=dns_host,
            kernel_hostname=kernel_hostname,
            canary=canary,
            rollout_order=rollout_order,
            enabled=enabled,
            concurrency=concurrency,
            memory_high=memory_high,
            memory_max=memory_max,
            cpu_quota=cpu_quota,
        )
        worker_nodes.add(topology_node)
        kernel_hostnames.add(kernel_hostname)
        rollout_orders.add(rollout_order)

    expected_orders = set(range(1, len(crawler_workers) + 1))
    if rollout_orders != expected_orders:
        raise ValueError("production topology crawler worker rolloutOrder must be contiguous")
    canaries = [worker for worker in crawler_workers.values() if worker.canary]
    if len(canaries) != 1 or canaries[0].rollout_order != 1:
        raise ValueError("production topology requires one first crawler canary")
    if crawler_mode == "legacy" and any(
        worker.enabled for worker in crawler_workers.values()
    ):
        raise ValueError("production topology legacy mode forbids enabled crawler workers")
    enabled_orders = sorted(
        worker.rollout_order
        for worker in crawler_workers.values()
        if worker.enabled
    )
    if enabled_orders and enabled_orders != list(range(1, enabled_orders[-1] + 1)):
        raise ValueError(
            "production topology enabled crawler workers must be a canary-first "
            "contiguous rollout prefix"
        )

    raw_services = payload["services"]
    if type(raw_services) is not dict or not 1 <= len(raw_services) <= _MAX_SERVICES:
        raise ValueError("production topology services are invalid")
    if not _REQUIRED_SERVICES.issubset(raw_services):
        raise ValueError("production topology is missing a required service")

    services: dict[str, tuple[ServicePlacement, ...]] = {}
    for raw_service, raw_placements in raw_services.items():
        service = _identifier(raw_service, field="service name")
        if type(raw_placements) is not list or not 1 <= len(raw_placements) <= _MAX_PLACEMENTS_PER_SERVICE:
            raise ValueError(f"production topology {service} placements are invalid")

        placements: list[ServicePlacement] = []
        placed_nodes: set[str] = set()
        for raw_placement in raw_placements:
            placement = _require_object(
                raw_placement,
                field=f"{service} placement",
                required=frozenset({"node", "role"}),
                optional=frozenset({"replicatesFrom"}),
            )
            node = _identifier(placement["node"], field=f"{service} node")
            if node not in nodes:
                raise ValueError(f"production topology {service} references an unknown node")
            if node in placed_nodes:
                raise ValueError(f"production topology {service} repeats a node")
            placed_nodes.add(node)

            role = placement["role"]
            if role not in {"primary", "standby"}:
                raise ValueError(f"production topology {service} role is invalid")
            raw_source = placement.get("replicatesFrom")
            if role == "primary" and raw_source is not None:
                raise ValueError(f"production topology {service} primary cannot replicate")
            if role == "standby" and raw_source is None:
                raise ValueError(f"production topology {service} standby needs a source")
            source = (
                _identifier(raw_source, field=f"{service} replication source")
                if raw_source is not None
                else None
            )
            if source is not None and source not in nodes:
                raise ValueError(f"production topology {service} replication source is unknown")
            placements.append(
                ServicePlacement(
                    service=service,
                    node=node,
                    service_host=nodes[node].dns_host,
                    role=role,
                    replicates_from=source,
                )
            )

        primary_nodes = [item.node for item in placements if item.role == "primary"]
        if len(primary_nodes) != 1:
            raise ValueError(f"production topology {service} must have one primary")
        primary_node = primary_nodes[0]
        if any(
            item.role == "standby" and item.replicates_from != primary_node
            for item in placements
        ):
            raise ValueError(f"production topology {service} standby source is not its primary")
        services[service] = tuple(
            sorted(placements, key=lambda item: item.role != "primary")
        )

    for service in _ACTIVE_NODE_SERVICES:
        primary = next(item for item in services[service] if item.role == "primary")
        if primary.node != active_node:
            raise ValueError(
                f"production topology {service} primary must be on activeNode"
            )

    control_primary = next(
        item
        for item in services[_CONTROL_PLANE_SERVICE]
        if item.role == "primary"
    )
    if control_primary.node == active_node:
        raise ValueError(
            "production topology crawler_control primary must not be on activeNode"
        )

    staging_primary = next(
        item
        for item in services[_STAGING_DATABASE_SERVICE]
        if item.role == "primary"
    )
    if staging_primary.node == active_node:
        raise ValueError(
            "production topology staging_database primary must not be on activeNode"
        )
    if staging_primary.node != control_primary.node:
        raise ValueError(
            "production topology staging_database primary must be co-located "
            "with crawler_control primary"
        )

    crawler_primary = next(
        item for item in services["crawler"] if item.role == "primary"
    )
    if crawler_primary.node not in worker_nodes:
        raise ValueError(
            "production topology crawler primary must have a reviewed worker assignment"
        )
    forbidden_worker_nodes = {active_node, control_primary.node, staging_primary.node}
    if worker_nodes & forbidden_worker_nodes:
        raise ValueError(
            "production topology crawler workers must not run on web or control nodes"
        )

    return ProductionTopology(
        schema_version=1,
        environment="production",
        active_node=active_node,
        crawler_mode=crawler_mode,
        nodes=MappingProxyType(nodes),
        crawler_workers=MappingProxyType(crawler_workers),
        services=MappingProxyType(services),
    )


def public_production_topology(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Load the manifest and return its safe operator-facing representation."""
    return load_production_topology(root).public_payload()


def production_service_placements(
    service: str,
    root: Path = PROJECT_ROOT,
) -> tuple[ServicePlacement, ...]:
    """Load the manifest and return the reviewed placements for one service."""
    return load_production_topology(root).placements_for(service)


__all__ = [
    "PRODUCTION_TOPOLOGY_PATH",
    "CrawlerWorker",
    "ProductionNode",
    "ProductionTopology",
    "ServicePlacement",
    "load_production_topology",
    "production_service_placements",
    "public_production_topology",
]
