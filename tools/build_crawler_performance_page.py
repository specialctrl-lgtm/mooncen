from __future__ import annotations

import argparse
import csv
import html
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_crawlers
from DB.db_utils import get_db_cursor


GENERATED_REGISTRY = ROOT / "config" / "generated_yaml_crawler_registry.yaml"
CRAWLER_REPORT_DIR = ROOT / "logs" / "crawler_reports"
MUNICIPAL_REPORT_DIR = ROOT / "logs" / "municipal_crawler_reports"
OUT_DIR = ROOT / "logs" / "crawler_performance_pages"

CORE_FIELDS = ["title", "branch", "raw_url"]
IMPORTANT_FIELDS = ["period", "schedule_raw", "fee", "status", "target", "description"]
META_PROVIDERS = {"COLLECTED_YAML", "FACILITY_REGISTRY", "YAML_TARGETS_ALL"}


def rel(path: str | Path) -> str:
    path = Path(path)
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def pct(count: int, total: int) -> float:
    return round((count / total) * 100, 1) if total else 0.0


def avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 1) if values else 0.0


def load_generated_registry() -> dict[str, dict[str, Any]]:
    if not GENERATED_REGISTRY.exists():
        return {}
    data = yaml.safe_load(GENERATED_REGISTRY.read_text(encoding="utf-8")) or {}
    targets = data.get("targets") or []
    return {
        str(row.get("provider") or "").strip().upper(): row
        for row in targets
        if isinstance(row, dict) and row.get("provider")
    }


def script_path_from_command(parts: list[str]) -> Path | None:
    for index, part in enumerate(parts):
        if str(part).endswith(".py"):
            return ROOT.joinpath(*parts[: index + 1])
    return None


def command_text(parts: list[str]) -> str:
    return "python -X utf8 " + " ".join(parts)


def latest_cycle_reports() -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not CRAWLER_REPORT_DIR.exists():
        return latest
    for path in sorted(CRAWLER_REPORT_DIR.glob("crawler_report_*.json"), key=lambda p: p.stat().st_mtime):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for row in data.get("providers") or []:
            if not isinstance(row, dict) or not row.get("provider"):
                continue
            provider = str(row["provider"]).strip().upper()
            latest[provider] = {
                **row,
                "_report": rel(path),
                "_report_mtime": path.stat().st_mtime,
            }
    return latest


def latest_municipal_reports() -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not MUNICIPAL_REPORT_DIR.exists():
        return latest
    for path in sorted(MUNICIPAL_REPORT_DIR.glob("municipal_yaml_crawler_*.yaml"), key=lambda p: p.stat().st_mtime):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        for row in data.get("reports") or []:
            if not isinstance(row, dict) or not row.get("provider"):
                continue
            provider = str(row["provider"]).strip().upper()
            latest[provider] = {
                **row,
                "_report": rel(path),
                "_report_mtime": path.stat().st_mtime,
            }
    return latest


def fetch_db_provider_stats(providers: list[str]) -> dict[str, dict[str, Any]]:
    if not providers:
        return {}
    stats: dict[str, dict[str, Any]] = {}
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT
                provider,
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE is_active IS TRUE) AS active_total,
                COUNT(*) FILTER (WHERE branch_id IS NOT NULL) AS branch_count,
                COUNT(*) FILTER (WHERE raw_url IS NOT NULL AND btrim(raw_url) <> '') AS raw_url_count,
                COUNT(*) FILTER (WHERE description IS NOT NULL AND btrim(description) <> '') AS description_count,
                COUNT(*) FILTER (WHERE image_url IS NOT NULL AND btrim(image_url) <> '') AS image_count,
                COUNT(*) FILTER (WHERE schedule_raw IS NOT NULL AND btrim(schedule_raw) <> '') AS schedule_count,
                COUNT(*) FILTER (WHERE target_age_group IS NOT NULL) AS target_age_count,
                COUNT(*) FILTER (WHERE fee IS NOT NULL) AS fee_count,
                COUNT(*) FILTER (WHERE status IS NOT NULL AND btrim(status) <> '') AS status_count
            FROM courses
            WHERE provider = ANY(%s)
            GROUP BY provider
            """,
            (providers,),
        )
        for row in cursor.fetchall():
            item = dict(row)
            provider = str(item["provider"]).strip().upper()
            total = int(item.get("total") or 0)
            stats[provider] = {
                "db_total": total,
                "db_active": int(item.get("active_total") or 0),
                "db_branch_pct": pct(int(item.get("branch_count") or 0), total),
                "db_raw_url_pct": pct(int(item.get("raw_url_count") or 0), total),
                "db_description_pct": pct(int(item.get("description_count") or 0), total),
                "db_image_pct": pct(int(item.get("image_count") or 0), total),
                "db_schedule_pct": pct(int(item.get("schedule_count") or 0), total),
                "db_target_age_pct": pct(int(item.get("target_age_count") or 0), total),
                "db_fee_pct": pct(int(item.get("fee_count") or 0), total),
                "db_status_pct": pct(int(item.get("status_count") or 0), total),
            }
    return stats


def report_quality_from_municipal(row: dict[str, Any]) -> dict[str, Any]:
    fields = row.get("fields") or {}
    collected = int(row.get("collected") or 0)
    core_pct = avg([pct(int(fields.get(field) or 0), collected) for field in CORE_FIELDS]) if collected else 0.0
    important_pct = avg([pct(int(fields.get(field) or 0), collected) for field in IMPORTANT_FIELDS]) if collected else 0.0
    return {
        "last_success": bool(row.get("success")),
        "last_elapsed": "",
        "last_rows": collected,
        "last_saved": int(row.get("saved") or 0),
        "last_pages": int(row.get("pages") or 0),
        "last_detail_pages": int(row.get("detail_pages") or 0),
        "last_parser": row.get("parser") or "",
        "last_core_pct": core_pct,
        "last_important_pct": important_pct,
        "last_error": row.get("error") or row.get("no_current_reason") or "",
        "last_report": row.get("_report") or "",
    }


def report_quality_from_cycle(row: dict[str, Any]) -> dict[str, Any]:
    quality = row.get("quality") or {}
    core_keys = ["title", "branch", "raw_url"]
    important_keys = ["description", "schedule_raw", "target_age_group", "fee", "status"]
    return {
        "last_success": bool(row.get("success")),
        "last_elapsed": row.get("elapsed_seconds") or "",
        "last_rows": row.get("updated_since") or row.get("created_since") or 0,
        "last_saved": "",
        "last_pages": "",
        "last_detail_pages": "",
        "last_parser": "",
        "last_core_pct": avg([float((quality.get(key) or {}).get("rate") or 0) for key in core_keys]),
        "last_important_pct": avg([float((quality.get(key) or {}).get("rate") or 0) for key in important_keys]),
        "last_error": "" if row.get("success") else f"exit_code={row.get('exit_code')}",
        "last_report": row.get("_report") or "",
    }


def status_for_row(row: dict[str, Any]) -> tuple[str, str]:
    if row["provider"] in META_PROVIDERS:
        return "SKIP", "meta runner; inspect child providers"
    if row["registry_enabled"] is False:
        return "SKIP", row["disabled_reason"] or "disabled in generated registry"
    if row["script_exists"] is False:
        return "FAIL", "crawler script missing"
    if row["registry_status"] in {"blocked", "needs_discovery", "needs_parser"}:
        return "SKIP", row["disabled_reason"] or row["registry_status"]
    if row["last_error"] in {"no_open_courses", "not_application_period", "no_current_data"}:
        return "WARN", row["last_error"]
    if row["last_error"]:
        return "FAIL", row["last_error"]
    if not row["last_report"]:
        return "WARN", "no runtime report found"
    if row["last_success"] is False:
        return "FAIL", "last run failed"
    if row["last_success"] is True and int(row["last_rows"] or 0) <= 0 and int(row["db_active"] or 0) <= 0:
        return "SKIP", "no active courses"
    if int(row["last_rows"] or 0) <= 0 and int(row["db_active"] or 0) <= 0:
        return "WARN", "no collected/runtime rows and no active DB rows"
    if float(row["last_core_pct"] or 0) < 100:
        return "WARN", "core field coverage below 100%"
    if float(row["last_important_pct"] or 0) < 60:
        return "WARN", "important field coverage below 60%"
    return "OK", ""


def build_rows() -> list[dict[str, Any]]:
    registry = load_generated_registry()
    cycle_reports = latest_cycle_reports()
    municipal_reports = latest_municipal_reports()
    providers = sorted(set(run_crawlers.PROVIDER_COMMANDS) | set(registry))
    db_stats = fetch_db_provider_stats(providers)
    rows: list[dict[str, Any]] = []
    for provider in providers:
        command_parts = run_crawlers.PROVIDER_COMMANDS.get(provider) or []
        script_path = script_path_from_command(command_parts)
        registry_row = registry.get(provider) or {}
        municipal = municipal_reports.get(provider)
        cycle = cycle_reports.get(provider)
        if municipal and cycle:
            report_quality = (
                report_quality_from_municipal(municipal)
                if float(municipal.get("_report_mtime") or 0) >= float(cycle.get("_report_mtime") or 0)
                else report_quality_from_cycle(cycle)
            )
        elif municipal:
            report_quality = report_quality_from_municipal(municipal)
        elif cycle:
            report_quality = report_quality_from_cycle(cycle)
        else:
            report_quality = {}
        row = {
            "provider": provider,
            "command": command_text(command_parts) if command_parts else str(registry_row.get("command") or ""),
            "script": rel(script_path) if script_path else str(registry_row.get("crawler") or ""),
            "script_exists": script_path.exists() if script_path else bool(registry_row.get("crawler")),
            "registry_status": str(registry_row.get("status") or registry_row.get("target_status") or ""),
            "registry_enabled": registry_row.get("enabled", True),
            "disabled_reason": str(registry_row.get("disabled_reason") or ""),
            "name": registry_row.get("name") or "",
            "url": registry_row.get("url") or "",
            "last_success": "",
            "last_elapsed": "",
            "last_rows": 0,
            "last_saved": "",
            "last_pages": "",
            "last_detail_pages": "",
            "last_parser": "",
            "last_core_pct": 0.0,
            "last_important_pct": 0.0,
            "last_error": "",
            "last_report": "",
            **report_quality,
            **{
                "db_total": 0,
                "db_active": 0,
                "db_branch_pct": 0.0,
                "db_raw_url_pct": 0.0,
                "db_description_pct": 0.0,
                "db_image_pct": 0.0,
                "db_schedule_pct": 0.0,
                "db_target_age_pct": 0.0,
                "db_fee_pct": 0.0,
                "db_status_pct": 0.0,
            },
            **db_stats.get(provider, {}),
        }
        row["grade"], row["reason"] = status_for_row(row)
        rows.append(row)
    return rows


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    fields = [
        "grade",
        "provider",
        "registry_status",
        "registry_enabled",
        "last_success",
        "last_elapsed",
        "last_rows",
        "last_saved",
        "last_pages",
        "last_detail_pages",
        "last_core_pct",
        "last_important_pct",
        "db_total",
        "db_active",
        "db_description_pct",
        "db_image_pct",
        "db_schedule_pct",
        "db_target_age_pct",
        "last_parser",
        "reason",
        "script",
        "last_report",
        "url",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def url_cell(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    safe_url = esc(url)
    return f"<a href='{safe_url}' target='_blank' rel='noopener noreferrer'>{safe_url}</a>"


def write_html(rows: list[dict[str, Any]], path: Path) -> None:
    summary = {
        "total": len(rows),
        "ok": sum(1 for row in rows if row["grade"] == "OK"),
        "warn": sum(1 for row in rows if row["grade"] == "WARN"),
        "fail": sum(1 for row in rows if row["grade"] == "FAIL"),
        "skip": sum(1 for row in rows if row["grade"] == "SKIP"),
        "enabled": sum(1 for row in rows if row["registry_enabled"] is not False),
        "with_runtime_report": sum(1 for row in rows if row["last_report"]),
    }
    table_rows = []
    for row in sorted(rows, key=lambda item: ({"FAIL": 0, "WARN": 1, "OK": 2, "SKIP": 3}.get(item["grade"], 9), item["provider"])):
        table_rows.append(
            "<tr>"
            f"<td><span class='badge {esc(row['grade'].lower())}'>{esc(row['grade'])}</span></td>"
            f"<td>{esc(row['provider'])}</td>"
            f"<td>{esc(row['registry_status'])}</td>"
            f"<td>{esc(row['last_success'])}</td>"
            f"<td>{esc(row['last_elapsed'])}</td>"
            f"<td>{esc(row['last_rows'])}</td>"
            f"<td>{esc(row['last_pages'])}</td>"
            f"<td>{esc(row['last_core_pct'])}%</td>"
            f"<td>{esc(row['last_important_pct'])}%</td>"
            f"<td>{esc(row['db_active'])}/{esc(row['db_total'])}</td>"
            f"<td>{esc(row['last_parser'])}</td>"
            f"<td>{esc(row['reason'])}</td>"
            f"<td class='url'>{url_cell(row.get('url'))}</td>"
            f"<td>{esc(row['script'])}</td>"
            f"<td>{esc(row['last_report'])}</td>"
            "</tr>"
        )
    html_text = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MoonCen Crawler Performance</title>
  <style>
    body {{ margin: 0; font-family: Arial, "Malgun Gothic", sans-serif; color: #17202a; background: #f6f8fa; }}
    header {{ padding: 24px 28px; background: #12343b; color: white; }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }}
    main {{ padding: 20px 28px 40px; }}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 18px; }}
    .metric {{ background: white; border: 1px solid #dde3ea; border-radius: 8px; padding: 12px; }}
    .metric strong {{ display: block; font-size: 24px; }}
    .table-wrap {{ overflow: auto; background: white; border: 1px solid #dde3ea; border-radius: 8px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #edf1f5; padding: 8px 10px; text-align: left; white-space: nowrap; vertical-align: top; }}
    th {{ position: sticky; top: 0; background: #eef3f6; z-index: 1; }}
    td.url {{ max-width: 520px; white-space: normal; word-break: break-all; }}
    td.url a {{ color: #075985; text-decoration: none; }}
    td.url a:hover {{ text-decoration: underline; }}
    .badge {{ display: inline-block; min-width: 42px; padding: 3px 7px; border-radius: 999px; text-align: center; font-weight: 700; }}
    .ok {{ background: #d9f99d; color: #365314; }}
    .warn {{ background: #fef3c7; color: #92400e; }}
    .fail {{ background: #fee2e2; color: #991b1b; }}
    .skip {{ background: #e5e7eb; color: #374151; }}
    .note {{ margin: 12px 0 0; color: #d7e7ea; }}
  </style>
</head>
<body>
  <header>
    <h1>MoonCen Crawler Performance</h1>
    <div>generated_at: {esc(datetime.now().isoformat(timespec='seconds'))}</div>
    <p class="note">Latest cycle/municipal reports and current DB quality snapshot are merged per provider.</p>
  </header>
  <main>
    <section class="summary">
      {''.join(f"<div class='metric'><span>{esc(k)}</span><strong>{esc(v)}</strong></div>" for k, v in summary.items())}
    </section>
    <section class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>grade</th><th>provider</th><th>registry</th><th>last ok</th><th>elapsed</th><th>rows</th><th>pages</th>
            <th>core</th><th>important</th><th>db active/total</th><th>parser</th><th>reason</th><th>target url</th><th>script</th><th>report</th>
          </tr>
        </thead>
        <tbody>
          {''.join(table_rows)}
        </tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""
    path.write_text(html_text, encoding="utf-8")


def markdown_table(rows: list[dict[str, Any]], limit: int = 40) -> str:
    headers = ["grade", "provider", "last", "elapsed", "rows", "pages", "core", "important", "db", "reason", "target url"]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    ordered = sorted(rows, key=lambda item: ({"FAIL": 0, "WARN": 1, "OK": 2, "SKIP": 3}.get(item["grade"], 9), item["provider"]))
    for row in ordered[:limit]:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["grade"]),
                    str(row["provider"]),
                    str(row["last_success"]),
                    str(row["last_elapsed"]),
                    str(row["last_rows"]),
                    str(row["last_pages"]),
                    f"{row['last_core_pct']}%",
                    f"{row['last_important_pct']}%",
                    f"{row['db_active']}/{row['db_total']}",
                    str(row["reason"]).replace("|", "/")[:120],
                    str(row.get("url") or "").replace("|", "/")[:160],
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an HTML performance page for every registered crawler")
    parser.add_argument("--limit", type=int, default=40, help="Rows to print to stdout markdown summary")
    args = parser.parse_args()
    rows = build_rows()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = OUT_DIR / f"crawler_performance_{timestamp}.html"
    csv_path = OUT_DIR / f"crawler_performance_{timestamp}.csv"
    json_path = OUT_DIR / f"crawler_performance_{timestamp}.json"
    write_html(rows, html_path)
    write_csv(rows, csv_path)
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(markdown_table(rows, args.limit))
    print()
    print(f"html={rel(html_path)}")
    print(f"csv={rel(csv_path)}")
    print(f"json={rel(json_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
