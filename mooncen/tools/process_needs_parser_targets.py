from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[1]
TARGET_DIR = ROOT / "config" / "crawl_targets"
REPORT_DIR = ROOT / "logs" / "municipal_crawler_reports"


CORE_FIELDS = ("title", "branch", "raw_url")
IMPORTANT_FIELDS = ("period", "schedule_raw", "fee", "status", "target", "description")

DEPRECATED_DOMAIN_TOKENS = (
    "asiatoday.co.kr",
    "blog.naver.com",
    "boeuni.com",
    "brcity.kr",
    "cctimes.kr",
    "cfnews.kr",
    "domin.co.kr",
    "elovejc.kr",
    "ggilbo.com",
    "gndomin.com",
    "gukjenews.com",
    "hyundaiilbo.com",
    "idaegu.co.kr",
    "igangbuk.com",
    "igimpo.com",
    "imedialife.co.kr",
    "jeollailbo.com",
    "jjn.co.kr",
    "jnilbo.com",
    "jntoday.co.kr",
    "joongdo.co.kr",
    "kbsm.net",
    "khan.co.kr",
    "kjilbo.co.kr",
    "kmaeil.com",
    "kwtotalnews.kr",
    "kyongbuk.co.kr",
    "mygoyang.com",
    "newsfire.co.kr",
    "newsro.kr",
    "pointe.co.kr",
    "seoulilbo.com",
    "tbs.seoul.kr",
    "todayan.com",
    "welfarehello.com",
    "yangsanilbo.com",
    "yg21.co.kr",
    "yongin21.co.kr",
    "zsick.com",
)

DEPRECATED_EXTENSIONS = (
    ".pdf",
    ".hwp",
    ".hwpx",
    ".xls",
    ".xlsx",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".zip",
)

NOTICE_URL_TOKENS = (
    "/news/",
    "/m_news/",
    "/notice/",
    "articleview",
    "bbsmsgdetail",
    "selectbbsdetail",
    "selectbbsnttview",
    "selectnttlist",
    "selectboardview",
    "common/bbs/selectbbsdetail",
    "/board/view.",
    "/board/view/",
    "doviewboarditem",
    "bbs/board.php",
    "/media/board/",
    "boardlist.do?boardid=",
    "cmmboardview.do",
    "selecteminwonnewsview.do",
    "notice?idx=",
    "mode=view",
    "articleseq=",
    "nttid=",
    "ntatcseq=",
)


def clean(value: object) -> str:
    return str(value or "").strip()


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def refresh_summary(data: dict) -> None:
    targets = [row for row in data.get("targets") or [] if isinstance(row, dict)]
    by_status: dict[str, int] = {}
    by_collection_type: dict[str, int] = {}
    for row in targets:
        status = clean(row.get("crawler_status") or row.get("status") or "unknown") or "unknown"
        collection_type = clean(row.get("collection_type") or "unknown") or "unknown"
        by_status[status] = by_status.get(status, 0) + 1
        by_collection_type[collection_type] = by_collection_type.get(collection_type, 0) + 1
    data["summary"] = {
        "targets": len(targets),
        "by_status": dict(sorted(by_status.items())),
        "by_collection_type": dict(sorted(by_collection_type.items())),
    }


def write_yaml(path: Path, data: dict) -> None:
    refresh_summary(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=140), encoding="utf-8")


def latest_report() -> Path:
    reports = sorted(REPORT_DIR.glob("municipal_yaml_crawler_*.yaml"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not reports:
        raise FileNotFoundError(f"No reports in {REPORT_DIR}")
    return reports[0]


def pct(value: int, total: int) -> float:
    return (value / total * 100.0) if total else 0.0


def report_grade(report: dict) -> str:
    if clean(report.get("error")):
        return "ERROR"
    collected = int(report.get("collected") or 0)
    if collected <= 0:
        return "NO_DATA"
    fields = report.get("fields") or {}
    core_present = sum(1 for field in CORE_FIELDS if int(fields.get(field) or 0) >= collected)
    important_present = sum(pct(int(fields.get(field) or 0), collected) for field in IMPORTANT_FIELDS)
    core_pct = core_present / len(CORE_FIELDS) * 100.0
    important_pct = important_present / len(IMPORTANT_FIELDS)
    if core_pct >= 100 and important_pct >= 60:
        return "A"
    if core_pct >= 100 and important_pct >= 35:
        return "B"
    if core_pct >= 100 and important_pct >= 15:
        return "C"
    return "D"


def report_key(provider: str, url: str) -> tuple[str, str]:
    return (provider.upper(), url.strip())


def is_deprecated_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    path = urlparse(url).path.lower()
    if any(token in host for token in DEPRECATED_DOMAIN_TOKENS):
        return True
    return path.endswith(DEPRECATED_EXTENSIONS)


def is_notice_like_url(url: str) -> bool:
    return any(token in url.lower() for token in NOTICE_URL_TOKENS)


def next_status(row: dict, report: dict | None) -> tuple[str, str]:
    url = clean(row.get("url") or row.get("list_url") or row.get("base_url"))
    if report:
        grade = report_grade(report)
        collected = int(report.get("collected") or 0)
        if collected > 0:
            if grade in {"A", "B"}:
                return "ready", f"needs_parser resolved by sample crawl grade={grade} rows={collected}"
            return "partial", f"needs_parser resolved with weak sample grade={grade} rows={collected}"
        if grade == "ERROR":
            return "blocked", f"needs_parser sample crawl error: {clean(report.get('error'))[:160]}"
    if is_deprecated_url(url):
        return "deprecated", "non-course media/document URL removed from needs_parser"
    if is_notice_like_url(url):
        return "needs_discovery", "notice/detail URL needs application/list URL discovery"
    return "needs_discovery", "sample crawl found no course rows; URL discovery required"


def move_to_deprecated(row: dict, deprecated_data: dict, reason: str) -> None:
    row["crawler_status"] = "deprecated"
    row["source_group"] = "deprecated"
    row["collection_category"] = deprecated_data.get("domain_category") or "deprecated"
    row["domain_category"] = deprecated_data.get("domain_category") or "deprecated"
    row["deprecated_reason"] = reason
    row["deprecated_at"] = datetime.now().isoformat(timespec="seconds")
    deprecated_data.setdefault("targets", []).append(row)


def process(report_path: Path, dry_run: bool) -> dict[str, int]:
    report_data = load_yaml(report_path)
    reports = {
        report_key(clean(row.get("provider")), clean(row.get("url"))): row
        for row in report_data.get("reports") or []
        if isinstance(row, dict)
    }

    deprecated_path = TARGET_DIR / "deprecated.yaml"
    deprecated_data = load_yaml(deprecated_path)
    counts = {"ready": 0, "partial": 0, "needs_discovery": 0, "blocked": 0, "deprecated": 0, "unchanged": 0}
    touched_paths: set[Path] = set()

    for path in sorted(TARGET_DIR.glob("*.yaml")):
        if path.name in {"index.yaml", "deprecated.yaml"}:
            continue
        data = load_yaml(path)
        targets = data.get("targets") or []
        kept = []
        changed = False
        for row in targets:
            if not isinstance(row, dict):
                kept.append(row)
                continue
            if clean(row.get("crawler_status") or row.get("status")).lower() != "needs_parser":
                kept.append(row)
                continue
            provider = clean(row.get("provider"))
            url = clean(row.get("url") or row.get("list_url") or row.get("base_url"))
            status, reason = next_status(row, reports.get(report_key(provider, url)))
            if status == "needs_parser":
                counts["unchanged"] += 1
                kept.append(row)
                continue
            row["crawler_status"] = status
            row["needs_parser_processed_at"] = report_data.get("generated_at") or ""
            row["needs_parser_reason"] = reason
            counts[status] = counts.get(status, 0) + 1
            changed = True
            if status == "deprecated":
                move_to_deprecated(row, deprecated_data, reason)
            else:
                kept.append(row)
        if changed:
            data["targets"] = kept
            touched_paths.add(path)
            if not dry_run:
                write_yaml(path, data)

    if counts["deprecated"] and not dry_run:
        write_yaml(deprecated_path, deprecated_data)
    counts["files_touched"] = len(touched_paths)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve YAML targets currently marked needs_parser using a municipal crawler sample report.")
    parser.add_argument("--report", type=Path, default=None, help="municipal_yaml_crawler_*.yaml report. Defaults to latest report.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report_path = args.report or latest_report()
    counts = process(report_path, args.dry_run)
    print(f"report={report_path}")
    for key, value in counts.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
