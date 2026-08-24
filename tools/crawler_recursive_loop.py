from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = ROOT / "tools" / "crawler_recursive_audit.py"
DISCOVERY_SCRIPT = ROOT / "tools" / "discover_application_urls.py"
REPORT_DIR = ROOT / "logs" / "crawler_audits"
STATE_PATH = REPORT_DIR / "crawler_recursive_state.json"
MAX_EVIDENCE_REPORT_BYTES = 32 * 1024 * 1024
CORE_FIELDS = ("title", "branch", "raw_url")
IMPORTANT_FIELDS = ("period", "schedule_raw", "fee", "status", "target", "description")


def load_provider_commands() -> set[str]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import run_crawlers  # type: ignore

    return set(run_crawlers.PROVIDER_COMMANDS)


def run_process(command: list[str], timeout: int | None = None) -> dict[str, Any]:
    started = time.time()
    popen_kwargs: dict[str, Any] = {
        "cwd": ROOT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(command, **popen_kwargs)
        output, _stderr = process.communicate(timeout=timeout)
        return {
            "command": command,
            "exit_code": process.returncode,
            "duration_seconds": round(time.time() - started, 2),
            "output_tail": (output or "")[-6000:],
        }
    except subprocess.TimeoutExpired as exc:
        try:
            terminate_process_tree(process)
        except OSError:
            process.kill()
        try:
            output, _stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _stderr = process.communicate()
        return {
            "command": command,
            "exit_code": None,
            "timed_out": True,
            "duration_seconds": round(time.time() - started, 2),
            "output_tail": (
                (output or "")[-6000:]
                if isinstance(output, str)
                else (exc.stdout or "")[-6000:] if isinstance(exc.stdout, str) else ""
            ),
        }


def terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
    finally:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def find_json_objects(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    start = 0
    while True:
        index = text.find("{", start)
        if index < 0:
            return objects
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            start = index + 1
            continue
        if isinstance(value, dict):
            objects.append(value)
        start = index + max(end, 1)


def report_grade(report: dict[str, Any]) -> str:
    if str(report.get("error") or "").strip():
        return "ERROR"
    collected = int(report.get("collected") or 0)
    if collected <= 0:
        return "NO_DATA"
    fields = report.get("field_counts") or report.get("fields") or {}
    core_present = sum(1 for field in CORE_FIELDS if int(fields.get(field) or 0) >= collected)
    important_present = sum(
        (int(fields.get(field) or 0) / collected * 100.0)
        for field in IMPORTANT_FIELDS
    )
    core_pct = core_present / len(CORE_FIELDS) * 100.0
    important_pct = important_present / len(IMPORTANT_FIELDS)
    if core_pct >= 100 and important_pct >= 60:
        return "A"
    if core_pct >= 100 and important_pct >= 35:
        return "B"
    if core_pct >= 100 and important_pct >= 15:
        return "C"
    return "D"


def normalize_quality(provider: str, report: dict[str, Any]) -> dict[str, Any]:
    quality = dict(report)
    quality["provider"] = str(quality.get("provider") or provider).upper()
    if not quality.get("collected") and quality.get("rows") is not None:
        quality["collected"] = quality.get("rows")
    quality["collected"] = int(quality.get("collected") or 0)
    fields = quality.get("field_counts") or quality.get("fields")
    if isinstance(fields, dict):
        if not fields.get("raw_url") and fields.get("application_url"):
            fields["raw_url"] = fields.get("application_url")
        if not fields.get("fee") and fields.get("fee_raw"):
            fields["fee"] = fields.get("fee_raw")
    if "saved" in quality:
        quality["saved"] = int(quality.get("saved") or 0)
    if not quality.get("grade"):
        quality["grade"] = report_grade(quality)
    return quality


def quality_status(exit_code: int | None, quality: dict[str, Any] | None) -> str:
    if exit_code is None:
        return "timeout"
    if quality:
        grade = str(quality.get("grade") or "").upper()
        collected = int(quality.get("collected") or 0)
        error = str(quality.get("error") or "").strip()
        if error:
            if re.search(
                r"(HTTPError:\s*(?:400|401|403|404|408|409|429|500|502|503|504)|"
                r"ReadTimeout|ConnectTimeout|SSLError|Strict TLS request failed|certificate verify failed|"
                r"NameResolutionError|ConnectionError)",
                error,
                re.IGNORECASE,
            ):
                return "site_or_access_error"
            return "error"
        if exit_code != 0:
            return "failed"
        if quality.get("success") is False:
            return "failed"
        if quality.get("no_current_data"):
            return "no_current_data"
        if collected <= 0:
            return "no_data"
        if grade in {"A", "B"}:
            return "passed"
        return "weak"
    if exit_code == 0:
        return "passed_without_quality"
    return "failed"


def next_action_for_status(status: str) -> str:
    return {
        "passed": "candidate_verified",
        "weak": "review_parser_or_fields",
        "no_data": "discover_or_write_parser",
        "no_current_data": "retry_when_new_courses",
        "error": "fix_runtime_error",
        "site_or_access_error": "retry_later_or_update_access_strategy",
        "timeout": "reduce_scope_or_timeout",
        "failed": "inspect_failure",
        "passed_without_quality": "add_quality_output",
    }.get(status, "inspect")


def quality_from_report_path(
    provider: str,
    path_text: str,
    *,
    strict_provider: bool = False,
) -> dict[str, Any] | None:
    report_path = Path(path_text.strip().strip('"').strip("'"))
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    report_path = report_path.resolve()
    try:
        report_path.relative_to(ROOT.resolve())
    except ValueError:
        return None
    if not report_path.exists():
        return None
    data = yaml.safe_load(report_path.read_text(encoding="utf-8")) or {}
    reports = data.get("reports") if isinstance(data, dict) else None
    if not isinstance(reports, list):
        return None
    provider_upper = provider.upper()
    candidates = [row for row in reports if isinstance(row, dict)]
    exact_matches = [
        row for row in candidates if str(row.get("provider") or "").upper() == provider_upper
    ]
    if strict_provider and len(exact_matches) != 1:
        return None
    exact_match = exact_matches[0] if exact_matches else None
    match = exact_match if exact_match is not None else candidates[0] if len(candidates) == 1 else None
    if not isinstance(match, dict) and candidates:
        summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
        field_counts: dict[str, int] = {}
        parsers: Counter[str] = Counter()
        sample_titles: list[str] = []
        for row in candidates:
            fields = row.get("field_counts") or row.get("fields") or {}
            if isinstance(fields, dict):
                for key, value in fields.items():
                    field_counts[key] = field_counts.get(key, 0) + int(value or 0)
            parser_name = str(row.get("parser") or "").strip()
            if parser_name:
                parsers[parser_name] += 1
            for sample in row.get("samples") or []:
                if isinstance(sample, dict) and sample.get("title"):
                    sample_titles.append(str(sample.get("title"))[:180])
                    break
        parser = ",".join(name for name, _count in parsers.most_common(3)) or "aggregate_reports"
        quality = normalize_quality(
            provider,
            {
                "provider": provider_upper,
                "collected": int(summary.get("collected") or sum(int(row.get("collected") or 0) for row in candidates)),
                "saved": int(summary.get("saved") or sum(int(row.get("saved") or 0) for row in candidates)),
                "parser": parser,
                "field_counts": field_counts,
            },
        )
        quality["report_path"] = str(report_path)
        quality["targets"] = int(summary.get("targets") or len(candidates))
        quality["success_targets"] = int(summary.get("success") or sum(1 for row in candidates if row.get("success")))
        quality["error_targets"] = int(summary.get("errors") or sum(1 for row in candidates if row.get("error")))
        if sample_titles:
            quality["sample_titles"] = sample_titles[:3]
        return quality
    if not isinstance(match, dict):
        return None
    quality = normalize_quality(provider, match)
    quality["report_path"] = str(report_path)
    return quality


def quality_from_text(provider: str, output: str) -> dict[str, Any] | None:
    summary_match = re.search(
        r"(?m)^provider=(?P<provider>\S+)\s+(?:rows|collected)=(?P<rows>\d+)\s+saved=(?P<saved>\d+)\s+parser=(?P<parser>\S+)",
        output,
    )
    if not summary_match:
        return None

    field_counts: dict[str, int] = {}
    field_match = re.search(r"(?m)^field_counts\s+(?P<fields>.+)$", output)
    if field_match:
        for key, value in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=(\d+)", field_match.group("fields")):
            field_counts[key] = int(value)
        if "fee" not in field_counts and "fee_raw" in field_counts:
            field_counts["fee"] = field_counts["fee_raw"]

    quality = normalize_quality(
        provider,
        {
            "provider": summary_match.group("provider"),
            "collected": int(summary_match.group("rows")),
            "saved": int(summary_match.group("saved")),
            "parser": summary_match.group("parser"),
            "field_counts": field_counts,
        },
    )
    sample_match = re.search(r"(?m)^-\s+(.+)$", output)
    if sample_match:
        quality["sample_titles"] = [sample_match.group(1).split(" / ", 1)[0][:180]]
    return quality


def analyze_run(provider: str, result: dict[str, Any]) -> dict[str, Any]:
    output = str(result.get("output_tail") or "")
    quality = None
    for item in reversed(find_json_objects(output)):
        if "collected" in item or "rows" in item or "field_counts" in item or "fields" in item:
            quality = normalize_quality(provider, item)
            break

    report_match = re.search(r"(?m)^report=(.+)$", output)
    if report_match:
        report_quality = quality_from_report_path(provider, report_match.group(1))
        if report_quality:
            quality = report_quality
    if not quality:
        quality = quality_from_text(provider, output)

    status = quality_status(result.get("exit_code"), quality)
    if status == "no_data" and re.search(r"Skipping expired|expired-only|expired_on_page", output, re.IGNORECASE):
        status = "no_current_data"
    return {
        "status": status,
        "next_action": next_action_for_status(status),
        "quality": quality,
    }


def resolve_evidence_report(path: Path, *, max_age_hours: int) -> Path:
    candidate = path if path.is_absolute() else ROOT / path
    candidate = candidate.resolve()
    logs_root = (ROOT / "logs").resolve()
    try:
        candidate.relative_to(logs_root)
    except ValueError as exc:
        raise ValueError(f"Evidence report must be inside {logs_root}: {candidate}") from exc
    if not candidate.is_file():
        raise ValueError(f"Evidence report does not exist: {candidate}")
    if candidate.stat().st_size > MAX_EVIDENCE_REPORT_BYTES:
        raise ValueError(f"Evidence report exceeds {MAX_EVIDENCE_REPORT_BYTES} bytes: {candidate}")
    age_seconds = max(0.0, time.time() - candidate.stat().st_mtime)
    if age_seconds > max_age_hours * 3600:
        raise ValueError(f"Evidence report is older than {max_age_hours} hours: {candidate}")
    return candidate


def run_from_evidence_report(provider: str, report_path: Path) -> dict[str, Any] | None:
    quality = quality_from_report_path(provider, str(report_path), strict_provider=True)
    if quality is None:
        return None
    status = quality_status(0, quality)
    return {
        "provider": provider,
        "work_reason": "needs_recursive_review",
        "kind": "evidence_report",
        "command": ["evidence-report", str(report_path)],
        "evidence_report": str(report_path),
        "exit_code": 0,
        "duration_seconds": 0.0,
        "output_tail": "",
        "analysis": {
            "status": status,
            "next_action": next_action_for_status(status),
            "quality": quality,
        },
    }


def build_discovery_command(provider: str, args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        "-X",
        "utf8",
        str(DISCOVERY_SCRIPT),
        "--provider",
        provider,
        "--limit",
        "1",
        "--max-candidates",
        str(args.discovery_max_candidates),
        "--min-score",
        str(args.discovery_min_score),
        "--timeout",
        str(args.discovery_timeout),
    ]


def analyze_discovery(provider: str, result: dict[str, Any], min_score: int) -> dict[str, Any]:
    output = str(result.get("output_tail") or "")
    report_match = re.search(r"(?m)^report=(.+)$", output)
    discovery: dict[str, Any] | None = None
    if report_match:
        report_path = Path(report_match.group(1).strip())
        if not report_path.is_absolute():
            report_path = ROOT / report_path
        if report_path.exists():
            data = yaml.safe_load(report_path.read_text(encoding="utf-8")) or {}
            rows = data.get("results") if isinstance(data, dict) else None
            if isinstance(rows, list):
                provider_upper = provider.upper()
                row = next(
                    (item for item in rows if str(item.get("provider") or "").upper() == provider_upper),
                    rows[0] if len(rows) == 1 else None,
                )
                if isinstance(row, dict):
                    candidates = row.get("candidates") if isinstance(row.get("candidates"), list) else []
                    recommendation = row.get("recommendation") if isinstance(row.get("recommendation"), dict) else {}
                    recommended_url = str(recommendation.get("recommended_url") or "")
                    best = next(
                        (
                            item
                            for item in candidates
                            if isinstance(item, dict)
                            and recommended_url
                            and str(item.get("final_url") or item.get("url") or "") == recommended_url
                        ),
                        candidates[0] if candidates and isinstance(candidates[0], dict) else {},
                    )
                    discovery = {
                        "provider": provider_upper,
                        "report_path": str(report_path),
                        "source_url": row.get("source_url"),
                        "recommended_action": row.get("recommended_action") or recommendation.get("action"),
                        "recommendation_confidence": recommendation.get("confidence"),
                        "recommendation_reason": recommendation.get("reason"),
                        "best_url": best.get("final_url") or best.get("url"),
                        "best_score": int(best.get("score") or 0),
                        "parse_ready": bool(best.get("parse_ready")),
                        "registration_schedule_ready": bool(best.get("registration_schedule_ready")),
                        "application_path_ready": bool(best.get("application_path_ready")),
                        "verdict": best.get("verdict"),
                        "host_allowed": bool(best.get("host_allowed")),
                        "candidate_count": len(candidates),
                        "error": row.get("error") or best.get("error") or "",
                    }

    if not discovery:
        status = "discovery_failed" if result.get("exit_code") else "discovery_without_report"
        return {
            "status": status,
            "next_action": "inspect_discovery_output",
            "discovery": None,
        }

    recommended_action = str(discovery.get("recommended_action") or "")
    if discovery.get("error"):
        status = "discovery_error"
        action = "fix_discovery_or_access"
    elif (
        recommended_action in {"replace_target", "canonicalize_target"}
        and discovery.get("verdict") == "verified"
        and discovery.get("host_allowed")
        and discovery.get("parse_ready")
        and discovery.get("registration_schedule_ready")
    ):
        status = "discovery_replacement_ready"
        action = "promote_or_update_target_url"
    elif recommended_action == "keep_current":
        status = "discovery_current_valid"
        action = "review_parser_or_missing_fields"
    elif recommended_action == "review_candidate":
        status = "discovery_candidate_review"
        action = "manual_url_review"
    elif recommended_action == "unresolved":
        status = "discovery_no_candidate"
        action = "manual_discovery_required"
    elif discovery.get("parse_ready"):
        status = "discovery_legacy_or_unverified_candidate"
        action = "manual_url_review"
    elif int(discovery.get("best_score") or 0) >= min_score:
        status = "discovery_candidate"
        action = "inspect_candidate_and_write_parser"
    elif int(discovery.get("candidate_count") or 0) > 0:
        status = "discovery_weak_candidate"
        action = "manual_url_review"
    else:
        status = "discovery_no_candidate"
        action = "manual_discovery_required"
    return {
        "status": status,
        "next_action": action,
        "discovery": discovery,
    }


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    command = [sys.executable, "-X", "utf8", str(AUDIT_SCRIPT)]
    if args.audit_no_compile:
        command.append("--no-compile")
    if args.include_large_registries:
        command.append("--include-large-registries")
    for provider in args.providers or []:
        command.extend(["--provider", provider])

    result = run_process(command, timeout=args.audit_timeout)
    if result.get("timed_out") or result.get("exit_code") != 0:
        output_tail = str(result.get("output_tail") or "")[-1000:]
        raise RuntimeError(f"Crawler audit failed: {output_tail}")
    output = str(result.get("output_tail") or "")
    matches = re.findall(r"(?m)\bjson=(.+?)\s+md=", output)
    if not matches:
        raise RuntimeError("Crawler audit did not print its JSON report path.")
    report_path = Path(matches[-1].strip()).resolve()
    try:
        report_path.relative_to(REPORT_DIR.resolve())
    except ValueError as exc:
        raise RuntimeError(f"Crawler audit report escaped the report directory: {report_path}") from exc
    if not report_path.is_file():
        raise RuntimeError(f"Crawler audit report was not created: {report_path}")
    audit = json.loads(report_path.read_text(encoding="utf-8"))
    audit["_audit_command"] = result
    return audit


def item_key(item: dict[str, Any]) -> str:
    provider = str(
        item.get("provider")
        or item.get("script")
        or item.get("crawler")
        or item.get("wrapper")
        or ""
    ).upper()
    reason = str(item.get("reason") or "unknown")
    return f"{reason}:{provider}"


def load_state(path: Path, reset: bool = False) -> dict[str, Any]:
    if reset or not path.exists():
        return {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "updated_at": "",
            "items": {},
        }
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Crawler loop state is unreadable; use --reset-state to replace it: {path}") from exc
    if not isinstance(state, dict):
        raise ValueError(f"Crawler loop state root must be an object: {path}")
    if not isinstance(state.get("items"), dict):
        raise ValueError(f"Crawler loop state items must be an object: {path}")
    state.setdefault("created_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    state.setdefault("updated_at", "")
    return state


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temporary_path.write_text(content, encoding="utf-8")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2))


def state_record_for_item(state: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
    record = state.get("items", {}).get(item_key(item))
    return record if isinstance(record, dict) else None


def is_state_blocked(item: dict[str, Any], state: dict[str, Any], args: argparse.Namespace) -> bool:
    if args.ignore_state:
        return False
    record = state_record_for_item(state, item)
    if not record:
        return False
    attempts = int(record.get("attempts") or 0)
    return attempts >= max(1, int(args.max_attempts or 1))


def select_work_items(
    audit: dict[str, Any],
    args: argparse.Namespace,
    state: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    provider_filter = {provider.upper() for provider in args.providers or []}
    reason_filter = set(args.reasons or [])
    selected: list[dict[str, Any]] = []
    skipped_by_state = 0

    for item in audit.get("work_queue", []):
        provider = str(item.get("provider") or "").upper()
        reason = str(item.get("reason") or "")
        if provider_filter and provider not in provider_filter:
            continue
        if reason_filter and reason not in reason_filter:
            continue
        if is_state_blocked(item, state, args):
            skipped_by_state += 1
            continue
        if provider:
            selected.append(item)
        if len(selected) >= args.max_providers_per_iteration:
            break
    return selected, {
        "skipped_by_state": skipped_by_state,
        "state_items": len(state.get("items", {})),
        "max_attempts": max(1, int(args.max_attempts or 1)),
    }


def build_provider_command(provider: str, args: argparse.Namespace) -> list[str]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import run_crawlers  # type: ignore

    command = run_crawlers.build_provider_command(provider, args.limit)
    if not args.save_db:
        command = [part for part in command if part != "--save-db"]
    return command


def build_work_item_command(
    item: dict[str, Any],
    args: argparse.Namespace,
    provider_commands: set[str],
) -> tuple[list[str] | None, str | None]:
    provider = str(item.get("provider") or "").upper()
    if provider in provider_commands:
        return build_provider_command(provider, args), None

    crawler = str(item.get("crawler") or "").strip()
    if not crawler:
        return None, "provider_not_directly_runnable"

    script_path = (ROOT / crawler).resolve()
    try:
        script_path.relative_to(ROOT.resolve())
    except ValueError:
        return None, "crawler_path_outside_project"
    if not script_path.exists():
        return None, "crawler_file_missing"

    command = [sys.executable, "-X", "utf8", str(script_path)]
    if args.save_db:
        command.append("--save-db")
    if args.limit is not None:
        command.extend(["--limit", str(args.limit)])
    return command, None


def write_loop_report(report: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{os.getpid()}"
    path = REPORT_DIR / f"crawler_recursive_loop_{stamp}.json"
    latest_path = REPORT_DIR / "crawler_recursive_loop_latest.json"
    text = json.dumps(report, ensure_ascii=False, indent=2)
    atomic_write_text(path, text)
    atomic_write_text(latest_path, text)
    return path


def summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    statuses: Counter[str] = Counter()
    actions: Counter[str] = Counter()
    for run in runs:
        if run.get("skipped"):
            status = str(run.get("reason") or "skipped")
            action = "skip"
        else:
            analysis = run.get("analysis") if isinstance(run.get("analysis"), dict) else {}
            status = str(analysis.get("status") or "unknown")
            action = str(analysis.get("next_action") or "inspect")
        statuses[status] += 1
        actions[action] += 1
    return {
        "total": len(runs),
        "by_status": dict(sorted(statuses.items())),
        "by_next_action": dict(sorted(actions.items())),
    }


def run_status(run: dict[str, Any]) -> str:
    if run.get("skipped"):
        return str(run.get("reason") or "skipped")
    analysis = run.get("analysis") if isinstance(run.get("analysis"), dict) else {}
    return str(analysis.get("status") or "unknown")


def run_next_action(run: dict[str, Any]) -> str:
    if run.get("skipped"):
        return "skip"
    analysis = run.get("analysis") if isinstance(run.get("analysis"), dict) else {}
    return str(analysis.get("next_action") or "inspect")


def update_state_for_run(state: dict[str, Any], item: dict[str, Any], run: dict[str, Any]) -> None:
    key = item_key(item)
    items = state.setdefault("items", {})
    previous = items.get(key) if isinstance(items.get(key), dict) else {}
    attempts = int(previous.get("attempts") or 0) + 1
    quality = None
    discovery = None
    analysis = run.get("analysis") if isinstance(run.get("analysis"), dict) else {}
    if isinstance(analysis, dict):
        quality = analysis.get("quality")
        discovery = analysis.get("discovery")
    items[key] = {
        "provider": str(item.get("provider") or "").upper(),
        "reason": item.get("reason"),
        "registry": item.get("registry"),
        "crawler": item.get("crawler"),
        "attempts": attempts,
        "last_status": run_status(run),
        "last_next_action": run_next_action(run),
        "last_exit_code": run.get("exit_code"),
        "last_duration_seconds": run.get("duration_seconds"),
        "last_seen_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "evidence_report": run.get("evidence_report"),
        "quality": quality,
        "discovery": discovery,
    }


def summarize_state(state: dict[str, Any]) -> dict[str, Any]:
    statuses: Counter[str] = Counter()
    actions: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for record in state.get("items", {}).values():
        if not isinstance(record, dict):
            continue
        statuses[str(record.get("last_status") or "unknown")] += 1
        actions[str(record.get("last_next_action") or "inspect")] += 1
        reasons[str(record.get("reason") or "unknown")] += 1
    return {
        "items": len(state.get("items", {})),
        "by_status": dict(sorted(statuses.items())),
        "by_next_action": dict(sorted(actions.items())),
        "by_reason": dict(sorted(reasons.items())),
    }


def int_between(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"expected {minimum}..{maximum}, got {parsed}")
        return parsed

    return parse


def float_between(minimum: float, maximum: float):
    def parse(value: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"expected a number, got {value!r}") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"expected {minimum}..{maximum}, got {parsed}")
        return parsed

    return parse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repeat crawler audits and optionally execute the next runnable providers."
    )
    parser.add_argument("--iterations", type=int_between(1, 100), default=1)
    parser.add_argument("--sleep-seconds", type=float_between(0, 3600), default=0.0)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Run selected provider crawlers between audits.")
    mode.add_argument(
        "--evidence-report",
        action="append",
        type=Path,
        help="Import exact-provider quality evidence from a recent report under logs/. Can be repeated.",
    )
    parser.add_argument(
        "--evidence-max-age-hours",
        type=int_between(1, 720),
        default=24,
        help="Maximum accepted age for an imported evidence report.",
    )
    parser.add_argument("--providers", nargs="+", help="Limit loop to these providers.")
    parser.add_argument(
        "--reasons",
        nargs="+",
        default=["deprecated_registry_stale", "needs_parser", "needs_recursive_review"],
        help="Work queue reasons to select.",
    )
    parser.add_argument("--max-providers-per-iteration", type=int_between(1, 500), default=1)
    parser.add_argument("--limit", type=int_between(1, 1000), default=1, help="Per-provider crawler limit when executing.")
    parser.add_argument("--save-db", action="store_true", help="Allow provider execution to write crawler results.")
    parser.add_argument("--provider-timeout", type=int_between(1, 3600), default=300)
    parser.add_argument("--audit-timeout", type=int_between(1, 3600), default=180)
    parser.add_argument("--audit-no-compile", action="store_true")
    parser.add_argument("--include-large-registries", action="store_true")
    parser.add_argument("--skip-post-audit", action="store_true", help="Do not run a second audit after execution.")
    parser.add_argument("--discover-needs-parser", action="store_true", help="Run URL discovery for needs_parser items.")
    parser.add_argument("--discovery-timeout", type=int_between(1, 120), default=15)
    parser.add_argument("--discovery-max-candidates", type=int_between(1, 100), default=5)
    parser.add_argument("--discovery-min-score", type=int_between(0, 100), default=60)
    parser.add_argument("--state-file", type=Path, default=STATE_PATH)
    parser.add_argument("--ignore-state", action="store_true", help="Do not skip items already attempted in the state file.")
    parser.add_argument("--reset-state", action="store_true", help="Start with an empty loop state file.")
    parser.add_argument(
        "--max-attempts",
        type=int_between(1, 100),
        default=1,
        help="Attempts allowed per provider/reason before skipping by state.",
    )
    parser.add_argument(
        "--allow-run-errors",
        action="store_true",
        help="Exit zero even when an executed crawler remains failed, weak, or otherwise actionable.",
    )
    args = parser.parse_args()
    if args.evidence_report and (args.save_db or args.discover_needs_parser):
        parser.error("--evidence-report cannot be combined with database writes or discovery")
    return args


def main() -> int:
    args = parse_args()
    try:
        evidence_reports = [
            resolve_evidence_report(path, max_age_hours=args.evidence_max_age_hours)
            for path in args.evidence_report or []
        ]
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    provider_commands = load_provider_commands()
    state = load_state(args.state_file if args.state_file.is_absolute() else ROOT / args.state_file, reset=args.reset_state)
    iterations = args.iterations
    loop_report: dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "execute": args.execute,
        "evidence_reports": [str(path) for path in evidence_reports],
        "state_file": str(args.state_file if args.state_file.is_absolute() else ROOT / args.state_file),
        "iterations": [],
    }
    state_changed = bool(args.reset_state)

    for index in range(1, iterations + 1):
        audit = run_audit(args)
        selected, selection_state = select_work_items(audit, args, state)
        iteration_report: dict[str, Any] = {
            "iteration": index,
            "audit_summary": audit.get("summary", {}),
            "selection_state": selection_state,
            "selected": selected,
            "runs": [],
        }

        print(
            "iteration={index} work_items={work_items} selected={selected_count}".format(
                index=index,
                work_items=audit.get("summary", {}).get("work_items"),
                selected_count=len(selected),
            )
        )

        iteration_state_updates = 0
        if args.execute or evidence_reports:
            for item in selected:
                provider = str(item.get("provider") or "").upper()
                if evidence_reports:
                    run = next(
                        (
                            evidence_run
                            for report_path in evidence_reports
                            if (evidence_run := run_from_evidence_report(provider, report_path)) is not None
                        ),
                        None,
                    )
                    if run is None:
                        run = {
                            "provider": provider,
                            "work_reason": item.get("reason"),
                            "skipped": True,
                            "reason": "provider_missing_from_evidence_reports",
                        }
                        iteration_report["runs"].append(run)
                        print(f"provider={provider} skipped=provider_missing_from_evidence_reports")
                        continue
                    run["work_reason"] = item.get("reason")
                    iteration_report["runs"].append(run)
                    update_state_for_run(state, item, run)
                    iteration_state_updates += 1
                    print(
                        "provider={provider} evidence_status={status}".format(
                            provider=provider,
                            status=run["analysis"]["status"],
                        )
                    )
                    continue
                if item.get("reason") == "deprecated_registry_stale":
                    run = {
                        "provider": provider,
                        "work_reason": item.get("reason"),
                        "skipped": True,
                        "reason": "registry_cleanup_required",
                    }
                    iteration_report["runs"].append(run)
                    update_state_for_run(state, item, run)
                    iteration_state_updates += 1
                    print(f"provider={provider} skipped=registry_cleanup_required")
                    continue
                if item.get("reason") == "needs_parser":
                    if args.discover_needs_parser:
                        command = build_discovery_command(provider, args)
                        print(f"provider={provider} discovering")
                        result = run_process(command, timeout=args.provider_timeout)
                        result["analysis"] = analyze_discovery(provider, result, args.discovery_min_score)
                        run = {"provider": provider, "work_reason": item.get("reason"), "kind": "discovery", **result}
                        iteration_report["runs"].append(run)
                        update_state_for_run(state, item, run)
                        iteration_state_updates += 1
                        print(
                            "provider={provider} discovery_status={status}".format(
                                provider=provider,
                                status=result["analysis"]["status"],
                            )
                        )
                        continue
                    run = {
                        "provider": provider,
                        "work_reason": item.get("reason"),
                        "skipped": True,
                        "reason": "parser_required",
                    }
                    iteration_report["runs"].append(run)
                    update_state_for_run(state, item, run)
                    iteration_state_updates += 1
                    print(f"provider={provider} skipped=parser_required")
                    continue
                command, skip_reason = build_work_item_command(item, args, provider_commands)
                if command is None:
                    run = {
                        "provider": provider,
                        "work_reason": item.get("reason"),
                        "skipped": True,
                        "reason": skip_reason,
                    }
                    iteration_report["runs"].append(run)
                    update_state_for_run(state, item, run)
                    iteration_state_updates += 1
                    print(f"provider={provider} skipped={skip_reason}")
                    continue
                print(f"provider={provider} running")
                result = run_process(command, timeout=args.provider_timeout)
                result["analysis"] = analyze_run(provider, result)
                run = {"provider": provider, "work_reason": item.get("reason"), **result}
                iteration_report["runs"].append(run)
                update_state_for_run(state, item, run)
                iteration_state_updates += 1
                print(
                    "provider={provider} exit_code={exit_code} status={status}".format(
                        provider=provider,
                        exit_code=result.get("exit_code"),
                        status=result["analysis"]["status"],
                    )
                )

            iteration_report["run_summary"] = summarize_runs(iteration_report["runs"])
            state_changed = state_changed or iteration_state_updates > 0
            if iteration_state_updates:
                save_state(args.state_file if args.state_file.is_absolute() else ROOT / args.state_file, state)
            if not args.skip_post_audit:
                post_audit = run_audit(args)
                iteration_report["post_audit_summary"] = post_audit.get("summary", {})
                print(
                    "post_audit work_items={work_items} syntax_errors={syntax_errors}".format(
                        work_items=post_audit.get("summary", {}).get("work_items"),
                        syntax_errors=post_audit.get("summary", {}).get("syntax_errors"),
                    )
                )

        loop_report["iterations"].append(iteration_report)
        loop_report["state_summary"] = summarize_state(state)
        report_path = write_loop_report(loop_report)
        if state_changed:
            save_state(args.state_file if args.state_file.is_absolute() else ROOT / args.state_file, state)
        print(f"loop_report={report_path}")

        if index < iterations and args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    if (args.execute or evidence_reports) and not args.allow_run_errors:
        accepted_statuses = {"passed", "no_current_data"}
        for iteration in loop_report["iterations"]:
            if any(run_status(run) not in accepted_statuses for run in iteration.get("runs", [])):
                return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
