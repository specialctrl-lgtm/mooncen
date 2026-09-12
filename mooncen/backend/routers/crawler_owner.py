from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend import models
from backend.database import get_db
from backend.ops.service import append_audit, current_environment
from backend.routers.auth import rate_limit, require_ops_operator, require_ops_viewer
from ops_agent.deployment_registry import DeployTarget, load_deploy_targets
from ops_agent.production_topology import load_production_topology


_REMOTE_HELPER = "/usr/local/libexec/mooncen-ops-service"
_STATUS_ARGUMENTS = (
    "/usr/bin/sudo",
    "-n",
    "--",
    _REMOTE_HELPER,
    "crawler-status",
)
_RUN_ARGUMENTS = (
    "/usr/bin/sudo",
    "-n",
    "--",
    _REMOTE_HELPER,
    "crawler-once-start",
)
_OUTPUT_LIMIT = 8_192


@dataclass(frozen=True)
class _OwnerControl:
    ssh: str
    target: DeployTarget
    identity: Path | None


class CrawlerOwnerRunRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=64)


_dispatch_lock = threading.Lock()
_dispatch_running = False
_dispatch_started_at: str | None = None
_dispatch_finished_at: str | None = None
_dispatch_exit_code: int | None = None
_dispatch_error: str | None = None


router = APIRouter(
    prefix="/api/ops/crawlers/owner",
    tags=["ops-crawler-owner"],
    dependencies=[Depends(rate_limit("ops-crawler-owner", 30, 60))],
)


def _bounded_output(value: str) -> str:
    return value.strip()[-_OUTPUT_LIMIT:]


def _resolve_identity(raw_value: str) -> Path | None:
    if raw_value.strip().lower() == "ssh-agent":
        return None
    expanded = os.path.expandvars(raw_value)
    if os.name == "nt":
        expanded = expanded.replace("$env:USERPROFILE", os.getenv("USERPROFILE", ""))
    path = Path(expanded).expanduser()
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
            raise ValueError
    except (OSError, ValueError) as exc:
        raise RuntimeError("reviewed crawler-owner SSH identity is unavailable") from exc
    return path.resolve()


def _owner_control() -> _OwnerControl:
    if current_environment() != "production":
        raise RuntimeError("crawler-owner control is available only for the production Ops environment")
    topology = load_production_topology()
    placement = topology.primary_for("crawler")
    _default, targets = load_deploy_targets()
    target = targets.get(placement.node)
    if (
        target is None
        or topology.crawler_mode != "legacy"
        or target.name != "gen1crawler"
        or target.server != placement.service_host
        or target.user != "sgm"
        or target.role != "crawler"
        or target.deploy_profile != "crawler-only"
        or target.active
    ):
        raise RuntimeError("reviewed crawler-owner target is unavailable")
    ssh = shutil.which("ssh")
    if not ssh:
        raise RuntimeError("OpenSSH client is unavailable")
    return _OwnerControl(ssh=ssh, target=target, identity=_resolve_identity(target.identity_file))


def _ssh_command(control: _OwnerControl, remote_arguments: tuple[str, ...]) -> list[str]:
    command = [
        control.ssh,
        "-o",
        "BatchMode=yes",
        "-o",
        "ProxyCommand=none",
        "-o",
        "ProxyJump=none",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "RequestTTY=no",
    ]
    if control.identity is not None:
        command.extend(["-i", str(control.identity), "-o", "IdentitiesOnly=yes"])
    command.append(f"{control.target.user}@{control.target.server}")
    command.extend(remote_arguments)
    return command


def _creation_flags() -> int:
    if os.name == "nt":
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return 0


def _parse_status(output: str) -> dict[str, dict[str, Any]]:
    units: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] = {}
    for line in [*output.splitlines(), ""]:
        if not line.strip():
            unit_id = str(current.get("Id") or "")
            if unit_id:
                units[unit_id] = current
            current = {}
            continue
        key, separator, value = line.partition("=")
        if separator and key in {
            "Id",
            "LoadState",
            "ActiveState",
            "SubState",
            "UnitFileState",
            "Result",
            "ExecMainStatus",
            "NextElapseUSecRealtime",
            "ExecMainStartTimestamp",
            "ExecMainExitTimestamp",
            "StateChangeTimestamp",
        }:
            current[key] = value
    return units


def _remote_status(control: _OwnerControl) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            _ssh_command(control, _STATUS_ARGUMENTS),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            check=False,
            creationflags=_creation_flags(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("crawler-owner status connection failed") from exc
    if completed.returncode != 0:
        detail = _bounded_output(completed.stderr) or "SSH status command failed"
        raise RuntimeError(detail)
    units = _parse_status(completed.stdout)
    timer = units.get("mooncen-crawler.timer")
    run = units.get("mooncen-crawler-once.service")
    if timer is None or run is None:
        raise RuntimeError("crawler-owner returned an incomplete service status")
    return {"timer": timer, "run": run}


def _dispatch_snapshot() -> dict[str, Any]:
    with _dispatch_lock:
        return {
            "running": _dispatch_running,
            "started_at": _dispatch_started_at,
            "finished_at": _dispatch_finished_at,
            "exit_code": _dispatch_exit_code,
            "error": _dispatch_error,
        }


def _launch_dispatch(control: _OwnerControl) -> dict[str, Any]:
    global _dispatch_running
    global _dispatch_started_at, _dispatch_finished_at
    global _dispatch_exit_code, _dispatch_error
    with _dispatch_lock:
        if _dispatch_running:
            raise RuntimeError("crawler-owner execution is already being dispatched")
        _dispatch_running = True
        _dispatch_started_at = datetime.now(timezone.utc).isoformat()
        _dispatch_finished_at = None
        _dispatch_exit_code = None
        _dispatch_error = None
    try:
        try:
            completed = subprocess.run(
                _ssh_command(control, _RUN_ARGUMENTS),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
                creationflags=_creation_flags(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("crawler-owner execution could not start") from exc
        if completed.returncode != 0:
            detail = _bounded_output(completed.stderr or completed.stdout) or "crawler-owner start command failed"
            raise RuntimeError(detail)
        return {"started_at": _dispatch_started_at, "accepted": True}
    finally:
        with _dispatch_lock:
            _dispatch_running = False
            _dispatch_finished_at = datetime.now(timezone.utc).isoformat()
            if "completed" in locals():
                _dispatch_exit_code = completed.returncode
                _dispatch_error = (
                    _bounded_output(completed.stderr or completed.stdout)
                    if completed.returncode
                    else None
                )
            else:
                _dispatch_exit_code = None
                _dispatch_error = "crawler-owner execution could not start"


@router.get("/status", dependencies=[Depends(require_ops_viewer)])
def crawler_owner_status() -> dict[str, Any]:
    try:
        control = _owner_control()
        remote = _remote_status(control)
    except RuntimeError as exc:
        return {
            "available": False,
            "owner": "gen1crawler",
            "reason": str(exc)[:500],
            "dispatch": _dispatch_snapshot(),
        }
    return {
        "available": True,
        "owner": control.target.name,
        "host": control.target.server,
        **remote,
        "dispatch": _dispatch_snapshot(),
    }


@router.post(
    "/run-all",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("ops-crawler-owner-run", 3, 300))],
)
def run_all_crawlers(
    payload: CrawlerOwnerRunRequest,
    request: Request,
    user: models.User = Depends(require_ops_operator),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if payload.confirmation != "MOONCEN-CRAWLER-ALL":
        raise HTTPException(status_code=422, detail="Production crawler confirmation is invalid")
    try:
        control = _owner_control()
        remote = _remote_status(control)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)[:500]) from exc
    run_state = str(remote["run"].get("ActiveState") or "")
    if run_state in {"activating", "active", "reloading"}:
        raise HTTPException(status_code=409, detail="The production crawler is already running")
    if _dispatch_snapshot()["running"]:
        raise HTTPException(status_code=409, detail="A production crawler dispatch is already active")

    append_audit(
        db,
        request,
        user_id=user.id,
        action="crawler.run_all.request",
        resource_type="crawler_owner",
        resource_id=control.target.name,
        after_data={"command": "crawler-once-start", "host": control.target.server},
        result="success",
    )
    db.commit()
    try:
        dispatch = _launch_dispatch(control)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "accepted": True,
        "owner": control.target.name,
        "dispatch": dispatch,
    }
