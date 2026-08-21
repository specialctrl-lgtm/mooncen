#!/usr/bin/env python3
"""Collect a minimal, non-sensitive Tailscale status snapshot.

The raw ``tailscale status --json`` document is kept in memory only.  This
module intentionally builds a new allowlisted document instead of deleting
known-sensitive keys from the raw response.
"""

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_EXCLUDED_NODES = frozenset({"ds1515", "ds718", "n100"})
MAX_COMMAND_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_NAME_LENGTH = 253
MAX_OS_LENGTH = 64


def utc_now_text():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_string(value, max_length):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > max_length:
        return ""
    if any(ord(character) < 32 for character in value):
        return ""
    return value


def canonical_timestamp(value):
    value = safe_string(value, 64)
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


def dns_name(node):
    if not isinstance(node, dict):
        return ""
    value = safe_string(node.get("DNSName"), MAX_NAME_LENGTH)
    return value.rstrip(".").split(".", 1)[0] if value else ""


def excluded_node_names(value=None):
    if value is None:
        value = os.environ.get("MONITOR_APP_EXCLUDED_NODES", "")
    additional = {
        item.strip().casefold()
        for item in str(value).split(",")
        if item.strip()
    }
    return DEFAULT_EXCLUDED_NODES | additional


def sanitize_node(node):
    if not isinstance(node, dict):
        return None
    alias = dns_name(node)
    name = safe_string(node.get("HostName"), MAX_NAME_LENGTH) or alias
    if not name:
        return None
    online = node.get("Online") is True
    active = node.get("Active") is True
    if not online:
        connection = "offline"
    elif not active:
        connection = "idle"
    elif isinstance(node.get("CurAddr"), str) and bool(node["CurAddr"].strip()):
        connection = "direct"
    elif isinstance(node.get("Relay"), str) and bool(node["Relay"].strip()):
        connection = "relay"
    else:
        connection = "unknown"
    return {
        "name": name,
        "dns_name": alias,
        "os": safe_string(node.get("OS"), MAX_OS_LENGTH) or "unknown",
        "online": online,
        "active": active,
        "connection": connection,
        "last_seen": canonical_timestamp(node.get("LastSeen")),
        "key_expiry": canonical_timestamp(node.get("KeyExpiry")),
    }


def sanitize_status(raw_status, *, generated_at=None, excluded_nodes=None):
    if not isinstance(raw_status, dict):
        raise ValueError("Tailscale status must be a JSON object")

    excluded = (
        excluded_node_names()
        if excluded_nodes is None
        else DEFAULT_EXCLUDED_NODES | {
            str(name).strip().casefold()
            for name in excluded_nodes
            if str(name).strip()
        }
    )
    peers_by_name = {}
    raw_peers = raw_status.get("Peer")
    if isinstance(raw_peers, dict):
        # Peer dictionary keys are node public keys.  Never copy or log them.
        for raw_peer in raw_peers.values():
            peer = sanitize_node(raw_peer)
            if peer and not {
                peer["name"].casefold(),
                peer["dns_name"].casefold(),
            }.intersection(excluded):
                peers_by_name[peer["name"].casefold()] = peer

    peers = sorted(peers_by_name.values(), key=lambda item: item["name"].casefold())
    online_count = sum(1 for peer in peers if peer["online"])
    self_node = sanitize_node(raw_status.get("Self"))
    if self_node and {
        self_node["name"].casefold(),
        self_node["dns_name"].casefold(),
    }.intersection(excluded):
        self_node = None

    return {
        "schema_version": 1,
        "generated_at": generated_at or utc_now_text(),
        "backend_state": safe_string(raw_status.get("BackendState"), 64) or "Unknown",
        "counts": {
            "total": len(peers),
            "online": online_count,
            "offline": len(peers) - online_count,
        },
        "self": self_node,
        "peers": peers,
    }


def collect_status(tailscale_path, timeout_seconds):
    result = subprocess.run(
        [tailscale_path, "status", "--json"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        raise RuntimeError("tailscale status command failed")
    if len(result.stdout) > MAX_COMMAND_OUTPUT_BYTES:
        raise RuntimeError("tailscale status response exceeds the size limit")
    try:
        return json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("tailscale status returned invalid JSON") from exc


def atomic_write_snapshot(output_path, snapshot):
    output = Path(output_path)
    if not output.is_absolute():
        raise ValueError("snapshot output path must be absolute")

    parent = output.parent
    parent_stat = parent.stat(follow_symlinks=False)
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise ValueError("snapshot parent must be a directory")

    descriptor = None
    temporary_path = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            dir=parent,
            prefix=f".{output.name}.",
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = None
            json.dump(snapshot, stream, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fchmod(stream.fileno(), 0o640)
            os.fsync(stream.fileno())
        os.replace(temporary_path, output)
        temporary_path = None

        if os.name == "posix":
            directory_flags = os.O_RDONLY
            if hasattr(os, "O_DIRECTORY"):
                directory_flags |= os.O_DIRECTORY
            directory_descriptor = os.open(parent, directory_flags)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tailscale", default="/usr/bin/tailscale")
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not os.path.isabs(args.tailscale):
        print("tailscale collector failed: executable path must be absolute", file=sys.stderr)
        return 2
    if args.timeout <= 0 or args.timeout > 60:
        print("tailscale collector failed: timeout must be between 0 and 60 seconds", file=sys.stderr)
        return 2
    try:
        raw_status = collect_status(args.tailscale, args.timeout)
        snapshot = sanitize_status(raw_status)
        atomic_write_snapshot(args.output, snapshot)
    except subprocess.TimeoutExpired:
        print("tailscale collector failed: command timed out", file=sys.stderr)
        return 1
    except Exception:
        # Do not expose raw command output, paths, identifiers, or JSON parser context.
        print("tailscale collector failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
