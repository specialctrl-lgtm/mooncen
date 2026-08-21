from __future__ import annotations

import argparse
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import yaml

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from Crawler.Crawler_MunicipalYaml import (
    CrawlTarget,
    MunicipalDbWriter,
    ProviderReport,
    collect_from_url,
    print_table,
    sample_rows,
    score_fields,
    write_report,
)
from DB.course_lifecycle import mark_stale_courses, utc_now
from service_group import infer_service_group
from utils import clean_text, setup_logger


ROOT = Path(__file__).resolve().parents[1]
TARGETS_FILE = ROOT / "config" / "collected_yaml_crawl_targets.yaml"
TARGET_DIR = ROOT / "config" / "crawl_targets"
REGISTRY_FILE = ROOT / "config" / "generated_yaml_crawler_registry.yaml"
EXCLUDED_URL_DOMAIN_TOKENS = ("e-ncom.co.kr",)
EXCLUDED_URL_PATH_TOKENS = (
    "/news/",
    "/m_news/",
    "/attaches/",
    "articleview",
    "articleView",
    "view.php?key=",
    "/notice/detail/",
    "bbsMsgDetail",
    "selectBbsDetail",
    "selectBbsNttView",
    "selectNttList",
    "selectBoardView",
    "common/bbs/selectBbsDetail",
    "/board/view.",
    "/board/view/",
    "board/download",
    "/download.do",
    "doViewBoardItem",
    "bbs/board.php?bo_table=notice",
    "/media/board/",
    "boardList.do?boardId=",
    "cmmBoardView.do",
    "selectEminwonNewsView.do",
    "notice?idx=",
    "mode=view",
    "articleSeq=",
    "nttId=",
    "ntatcSeq=",
    "openData/view",
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
EXCLUDED_URL_MEDIA_DOMAINS = (
    "asiatoday.co.kr",
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
    "pointe.co.kr",
    "seoulilbo.com",
    "todayan.com",
    "welfarehello.com",
    "yangsanilbo.com",
    "yg21.co.kr",
    "yongin21.co.kr",
    "zsick.com",
)
WORKING_CRAWLER_STATUSES = {"ready", "partial", "candidate", "generated"}
REGISTRY_CRAWLER_STATUSES = {"ready", "partial", "needs_discovery", "needs_parser", "blocked", "candidate", "generated"}
DISABLED_REGISTRY_STATUSES = {"blocked", "needs_discovery", "needs_parser"}
DEDICATED_PROVIDER_NAMES = {
    "HOMEPLUS",
    "EMART",
    "LOTTE",
    "HYUNDAI_DEPT",
    "GALLERIA",
    "AK_PLAZA",
    "ELAND_RETAIL",
    "SHINSEGAE_ACADEMY",
    "LOTTE_MART",
    "BUSAN_RESERVATION",
    "SAHASILVER_COURSE",
    "SEOSAN_WELFARE_TOTAL_RESERVATION",
    "SEONGNAM_BAEUMSOOP",
    "ANYANG_LIFELONG_LEARNING",
    "YONGIN_LIFELONG_LEARNING",
    "ESONGPA_SPORTS_CULTURE",
    "SEOUL_PUBLIC_SERVICE",
}
DEDICATED_PROVIDER_COMMANDS = {
    "ANYANG_LIFELONG_LEARNING": "python -X utf8 Crawler/Crawler_AnyangLearning.py --save-db",
}
GENERATED_PROVIDER_COMMAND_OVERRIDES = {
    "MUNI_LIB_GWE_GO_KR_303FFE72": (
        "python -X utf8 Crawler/generated_yaml/MUNI_LIB_GWE_GO_KR_303FFE72.py "
        "--save-db --max-pages 3 --detail-limit 20"
    ),
    "MUNI_YEYAK_DOBONGSISEOL_OR_KR_0A2D506D": (
        "python -X utf8 Crawler/generated_yaml/MUNI_YEYAK_DOBONGSISEOL_OR_KR_0A2D506D.py "
        "--save-db --max-pages 120 --detail-limit 1000"
    ),
    "MUNI_WWW_MICHUHOL_GO_KR_29D0C0F5": (
        "python -X utf8 Crawler/generated_yaml/MUNI_WWW_MICHUHOL_GO_KR_29D0C0F5.py "
        "--save-db --max-pages 10 --detail-limit 500"
    ),
    "MUNI_LLL_BUSAN_GO_KR_944C621B": (
        "python -X utf8 Crawler/generated_yaml/MUNI_LLL_BUSAN_GO_KR_944C621B.py "
        "--save-db --max-pages 5"
    ),
    "MUNI_WWW_SB_GO_KR_FF615DE7": (
        "python -X utf8 Crawler/generated_yaml/MUNI_WWW_SB_GO_KR_FF615DE7.py "
        "--save-db --max-pages 30 --detail-limit 1200"
    ),
    "MUNI_WWW_JPYOUTH_CO_KR_5E838FBF": (
        "python -X utf8 Crawler/generated_yaml/MUNI_WWW_JPYOUTH_CO_KR_5E838FBF.py "
        "--save-db --per-target-limit 0 --max-pages 14 --detail-limit 250"
    ),
}
DUPLICATE_QUERY_DROP_PARAMS = {
    "_",
    "callback",
    "currentpage",
    "currentpageno",
    "nowpage",
    "page",
    "pageindex",
    "pageno",
    "pageunit",
    "pagesize",
    "recordcountperpage",
    "rows",
    "timestamp",
    "token",
    "ts",
    "viewtype",
}

logger = setup_logger(__name__, "logs/crawler_generated_yaml_targets.log")


def safe_module_name(provider: str) -> str:
    name = re.sub(r"[^A-Z0-9_]+", "_", provider.upper()).strip("_")
    if not name:
        raise ValueError("Empty provider name")
    if name[0].isdigit():
        name = f"PROVIDER_{name}"
    return name


def target_url(target: dict[str, Any]) -> str:
    return clean_text(target.get("url") or target.get("list_url") or target.get("base_url"))


def normalized_duplicate_url(url: str) -> str:
    text = clean_text(url)
    if not text:
        return ""
    parsed = urlparse(text)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    path = re.sub(r"/+", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query_pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in DUPLICATE_QUERY_DROP_PARAMS:
            continue
        query_pairs.append((key, value))
    query = urlencode(sorted(query_pairs), doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


def target_scope_keys(target: dict[str, Any]) -> list[str]:
    explicit_scope = clean_text(target.get("crawl_scope") or target.get("collection_scope"))
    if explicit_scope:
        return [f"scope:{explicit_scope.lower()}"]
    urls: list[str] = []
    for key in ("url", "list_url", "base_url"):
        value = clean_text(target.get(key))
        if value:
            urls.append(value)
    for value in target.get("list_urls") or []:
        if clean_text(value):
            urls.append(clean_text(value))
    keys = [normalized_duplicate_url(url) for url in urls]
    return [key for key in dict.fromkeys(keys) if key]


def explicit_duplicate_reason(target: dict[str, Any]) -> str:
    duplicate_of = clean_text(target.get("duplicate_of"))
    if duplicate_of:
        return f"duplicate_of:{duplicate_of}"
    blocked_reason = clean_text(target.get("blocked_reason"))
    if blocked_reason.lower().startswith("duplicate_of:"):
        return blocked_reason
    error_kind = clean_text((target.get("last_quality") or {}).get("error_kind") if isinstance(target.get("last_quality"), dict) else "")
    if error_kind.lower().startswith("duplicate_of:"):
        return error_kind
    if clean_text(target.get("collection_type")).lower() == "duplicate":
        return "duplicate_collection_type"
    return ""


def url_has_excluded_domain(url: str) -> bool:
    value = url.lower()
    return any(token in value for token in EXCLUDED_URL_DOMAIN_TOKENS + EXCLUDED_URL_MEDIA_DOMAINS)


def url_has_excluded_path(url: str) -> bool:
    return any(token.lower() in url.lower() for token in EXCLUDED_URL_PATH_TOKENS)


def _load_target_rows(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    targets = data.get("targets") or []
    if not isinstance(targets, list):
        raise ValueError(f"Invalid target file: {path}")
    defaults = {
        key: clean_text(data.get(key))
        for key in ("collection_category", "domain_category", "source_group", "operator_type", "service_group")
        if clean_text(data.get(key))
    }
    rows: list[dict[str, Any]] = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        merged = {**defaults, **target}
        rows.append(merged)
    return rows


def _iter_target_rows(path: Path) -> list[dict[str, Any]]:
    if path == TARGETS_FILE and TARGET_DIR.exists():
        rows: list[dict[str, Any]] = []
        for target_file in sorted(TARGET_DIR.glob("*.yaml")):
            if target_file.name == "index.yaml":
                continue
            for row in _load_target_rows(target_file):
                row.setdefault("_target_file", target_file.name)
                rows.append(row)
        return rows
    return _load_target_rows(path)


def _is_working_target(target: dict[str, Any], extra_statuses: Optional[set[str]] = None) -> bool:
    provider = clean_text(target.get("provider")).upper()
    if provider in DEDICATED_PROVIDER_NAMES:
        return False
    if explicit_duplicate_reason(target):
        return False
    status = clean_text(target.get("crawler_status") or target.get("status")).lower()
    url = target_url(target)
    if not url:
        return False
    if url_has_excluded_domain(url):
        return False
    if url_has_excluded_path(url):
        return False
    allowed_statuses = set(WORKING_CRAWLER_STATUSES)
    if extra_statuses:
        allowed_statuses.update(extra_statuses)
    return status in allowed_statuses


def _is_registry_target(target: dict[str, Any]) -> bool:
    provider = clean_text(target.get("provider")).upper()
    if provider in DEDICATED_PROVIDER_NAMES:
        return False
    status = clean_text(target.get("crawler_status") or target.get("status")).lower()
    url = target_url(target)
    if not provider or not url:
        return False
    if url_has_excluded_domain(url):
        return False
    return status in REGISTRY_CRAWLER_STATUSES


def load_yaml_targets(path: Path = TARGETS_FILE, extra_statuses: Optional[set[str]] = None) -> list[dict[str, Any]]:
    return dedupe_targets([target for target in _iter_target_rows(path) if _is_working_target(target, extra_statuses=extra_statuses)])


def load_registry_targets(path: Path = TARGETS_FILE) -> list[dict[str, Any]]:
    return [target for target in _iter_target_rows(path) if _is_registry_target(target)]


def target_preference_key(target: dict[str, Any]) -> tuple[int, int, str, str]:
    status = clean_text(target.get("crawler_status") or target.get("status")).lower()
    status_rank = {"ready": 0, "partial": 1, "generated": 2, "candidate": 3, "needs_parser": 4, "needs_discovery": 5, "blocked": 6}.get(status, 9)
    return (
        int(target.get("priority") or 9),
        status_rank,
        clean_text(target.get("source")),
        clean_text(target.get("provider")),
    )


def dedupe_targets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for item in sorted(items, key=target_preference_key):
        provider = clean_text(item.get("provider"))
        keys = target_scope_keys(item)
        duplicate_owner = next((seen[key] for key in keys if key in seen), "")
        if duplicate_owner:
            logger.info("Skipping duplicate target %s; overlaps with %s", provider, duplicate_owner)
            continue
        for key in keys:
            seen[key] = provider
        selected.append(item)
    return selected


def to_crawl_target(item: dict[str, Any]) -> CrawlTarget:
    provider = clean_text(item.get("provider"))[:50]
    name = clean_text(item.get("name")) or provider
    branch = clean_text(item.get("branch")) or name
    return CrawlTarget(
        provider=provider,
        name=name,
        branch=branch,
        url=target_url(item),
        source=clean_text(item.get("source")),
        priority=int(item.get("priority") or 9),
        region=clean_text(item.get("region")),
        extra=item,
    )


def apply_target_metadata(rows: list[dict[str, Any]], target: CrawlTarget) -> None:
    metadata_keys = (
        "collection_category",
        "domain_category",
        "source_group",
        "operator_type",
        "service_group",
        "collection_type",
    )
    metadata = {key: clean_text(target.extra.get(key)) for key in metadata_keys if clean_text(target.extra.get(key))}
    if "collection_category" not in metadata and metadata.get("domain_category"):
        metadata["collection_category"] = metadata["domain_category"]
    metadata.setdefault(
        "service_group",
        infer_service_group(
            provider=target.provider,
            collection_category=metadata.get("collection_category"),
            domain_category=metadata.get("domain_category"),
            source_group=metadata.get("source_group"),
            operator_type=metadata.get("operator_type"),
            branch_name=target.branch,
            raw_url=target.url,
        ),
    )
    if not metadata:
        return
    for row in rows:
        for key, value in metadata.items():
            row.setdefault(key, value)


def select_targets(
    items: list[dict[str, Any]],
    providers: Optional[list[str]],
    source: Optional[str],
    max_priority: Optional[int],
    offset: int,
    limit: Optional[int],
) -> list[CrawlTarget]:
    selected: list[CrawlTarget] = []
    provider_set = {provider.upper() for provider in providers or []}
    for item in items:
        provider = clean_text(item.get("provider")).upper()
        if provider_set and provider not in provider_set:
            continue
        if source and clean_text(item.get("source")) != source:
            continue
        if max_priority is not None and int(item.get("priority") or 9) > max_priority:
            continue
        if not clean_text(item.get("url") or item.get("list_url") or item.get("base_url")):
            continue
        selected.append(to_crawl_target(item))

    selected.sort(key=lambda target: (target.priority, target.source, target.provider))
    if offset:
        selected = selected[offset:]
    if limit is not None:
        selected = selected[:limit]
    return selected


def build_registry(items: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    seen_scope_keys: dict[str, str] = {}
    for index, item in enumerate(sorted(items, key=target_preference_key), start=1):
        provider = clean_text(item.get("provider"))[:50]
        status = clean_text(item.get("crawler_status") or item.get("status")).lower()
        url = target_url(item)
        module_name = safe_module_name(provider)
        crawler = f"Crawler/generated_yaml/{module_name}.py"
        duplicate_reason = explicit_duplicate_reason(item)
        duplicate_owner = ""
        scope_keys = target_scope_keys(item)
        for scope_key in scope_keys:
            if scope_key in seen_scope_keys:
                duplicate_owner = seen_scope_keys[scope_key]
                break
        enabled = status in WORKING_CRAWLER_STATUSES and not url_has_excluded_path(url) and not duplicate_reason and not duplicate_owner
        disabled_reason = ""
        if duplicate_reason:
            disabled_reason = duplicate_reason
        elif duplicate_owner:
            disabled_reason = f"duplicate_url:{duplicate_owner}"
        elif status in DISABLED_REGISTRY_STATUSES:
            disabled_reason = status
        elif url_has_excluded_path(url):
            disabled_reason = "excluded_url_shape"
        if not duplicate_reason and not duplicate_owner:
            for scope_key in scope_keys:
                seen_scope_keys.setdefault(scope_key, provider)
        command = GENERATED_PROVIDER_COMMAND_OVERRIDES.get(
            provider,
            DEDICATED_PROVIDER_COMMANDS.get(
                provider,
                f"python -X utf8 {crawler} --save-db",
            ),
        )
        command = DEDICATED_PROVIDER_COMMANDS.get(
            provider,
            command,
        )
        crawler = "Crawler/Crawler_AnyangLearning.py" if provider in DEDICATED_PROVIDER_COMMANDS else crawler
        rows.append(
            {
                "index": index,
                "provider": provider,
                "name": clean_text(item.get("name")),
                "source": clean_text(item.get("source")),
                "priority": int(item.get("priority") or 9),
                "url": url,
                "crawler": crawler,
                "command": command,
                "status": status,
                "target_status": status,
                "enabled": enabled,
                "disabled_reason": disabled_reason,
            }
        )
    by_source: dict[str, int] = {}
    by_priority: dict[int, int] = {}
    by_status: dict[str, int] = {}
    for row in rows:
        by_source[row["source"]] = by_source.get(row["source"], 0) + 1
        by_priority[row["priority"]] = by_priority.get(row["priority"], 0) + 1
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    return {
        "version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_file": str(TARGET_DIR.as_posix() if TARGET_DIR.exists() else TARGETS_FILE.as_posix()),
        "summary": {
            "targets": len(rows),
            "by_source": by_source,
            "by_priority": dict(sorted(by_priority.items())),
            "by_status": dict(sorted(by_status.items())),
        },
        "targets": rows,
    }


def write_registry(path: Path = REGISTRY_FILE) -> Path:
    items = load_registry_targets()
    data = build_registry(items)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=140), encoding="utf-8")
    return path


def run_single_target(
    target: CrawlTarget,
    per_target_limit: int,
    save_db: bool,
    mark_stale: bool,
    max_depth: int,
    max_pages: int,
    detail_limit: int,
    timeout: int,
) -> ProviderReport:
    report = ProviderReport(provider=target.provider, name=target.name, url=target.url)
    try:
        collect_target = target
        if per_target_limit > 0:
            collect_target = CrawlTarget(
                provider=target.provider,
                name=target.name,
                branch=target.branch,
                url=target.url,
                source=target.source,
                priority=target.priority,
                region=target.region,
                extra={**target.extra, "per_target_limit": per_target_limit},
            )
        rows, parser, meta = collect_from_url(
            collect_target,
            timeout=timeout,
            max_depth=max_depth,
            max_pages=max_pages,
            detail_limit=detail_limit,
        )
        if per_target_limit > 0:
            rows = rows[:per_target_limit]
        apply_target_metadata(rows, target)
        branch_normalizer = MunicipalDbWriter(target.provider)
        for row in rows:
            branch_normalizer.normalize_branch_split_row(row)
        report.parser = parser
        report.collected = len(rows)
        report.pages = int(meta.get("pages") or 0)
        report.detail_pages = int(meta.get("detail_pages") or 0)
        report.discovered_links = int(meta.get("discovered_links") or 0)
        report.reservation_discovery_links = int(meta.get("reservation_discovery_links") or 0)
        report.reservation_fallback_pages = int(meta.get("reservation_fallback_pages") or 0)
        report.pagination_detected = bool(meta.get("pagination_detected"))
        report.recursion_depth = int(meta.get("recursion_depth") or 0)
        report.no_current_data = bool(meta.get("no_current_data"))
        report.no_current_reason = clean_text(meta.get("no_current_reason"))
        report.fields = score_fields(rows)
        report.samples = sample_rows(rows)
        report.success = bool(rows) or report.no_current_data
        stale_cutoff = utc_now()
        if rows and save_db:
            writer = MunicipalDbWriter(target.provider)
            report.saved = writer.save_rows(rows)
            if mark_stale and report.saved > 0 and per_target_limit <= 0:
                mark_stale_courses(target.provider, stale_cutoff)
        elif save_db and mark_stale and report.no_current_data:
            report.saved = 0
            mark_stale_courses(target.provider, stale_cutoff)
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {exc}"
        logger.error(
            "Generated YAML target failed provider=%s name=%s url=%s error=%s",
            target.provider,
            target.name,
            target.url,
            report.error,
            exc_info=True,
        )
    return report


def run_targets(
    targets: list[CrawlTarget],
    per_target_limit: int,
    save_db: bool,
    mark_stale: bool,
    max_depth: int,
    max_pages: int,
    detail_limit: int,
    timeout: int,
    parallel_workers: int = 1,
) -> list[ProviderReport]:
    if parallel_workers > 1 and len(targets) > 1:
        reports_by_index: dict[int, ProviderReport] = {}
        with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
            futures = {
                executor.submit(
                    run_single_target,
                    target,
                    per_target_limit,
                    save_db,
                    mark_stale,
                    max_depth,
                    max_pages,
                    detail_limit,
                    timeout,
                ): (index, target)
                for index, target in enumerate(targets, start=1)
            }
            for future in as_completed(futures):
                index, target = futures[future]
                try:
                    report = future.result()
                except Exception as exc:
                    report = ProviderReport(provider=target.provider, name=target.name, url=target.url, error=f"{type(exc).__name__}: {exc}")
                reports_by_index[index] = report
                logger.info(
                    "[%s/%s] %s collected=%s saved=%s parser=%s error=%s",
                    index,
                    len(targets),
                    target.provider,
                    report.collected,
                    report.saved,
                    report.parser,
                    report.error,
                )
        return [reports_by_index[index] for index in sorted(reports_by_index)]

    reports: list[ProviderReport] = []
    for index, target in enumerate(targets, start=1):
        report = run_single_target(
            target,
            per_target_limit=per_target_limit,
            save_db=save_db,
            mark_stale=mark_stale,
            max_depth=max_depth,
            max_pages=max_pages,
            detail_limit=detail_limit,
            timeout=timeout,
        )
        reports.append(report)
        logger.info(
            "[%s/%s] %s collected=%s saved=%s parser=%s error=%s",
            index,
            len(targets),
            target.provider,
            report.collected,
            report.saved,
            report.parser,
            report.error,
        )
    return reports


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generated crawler runner for every provider in collected_yaml_crawl_targets.yaml")
    parser.add_argument("--provider", action="append", help="Provider to run. Can be repeated.")
    parser.add_argument("--all", action="store_true", help="Run all selected generated YAML providers")
    parser.add_argument("--source", help="Filter by YAML source name")
    parser.add_argument("--max-priority", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--target-limit", type=int, default=None)
    parser.add_argument("--per-target-limit", type=int, default=50)
    parser.add_argument("--limit", type=int, default=None, help="Alias for --per-target-limit")
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--detail-limit", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--parallel-workers", type=int, default=1, help="Run YAML targets concurrently for quality audits")
    parser.add_argument("--write-registry", action="store_true")
    parser.add_argument(
        "--include-status",
        action="append",
        default=[],
        help="Also include targets with this crawler_status/status. Can be repeated, e.g. --include-status needs_parser.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.write_registry:
        path = write_registry()
        print(f"registry={path}")

    items = load_yaml_targets(extra_statuses={clean_text(status).lower() for status in args.include_status})
    targets = select_targets(
        items,
        providers=args.provider,
        source=args.source,
        max_priority=args.max_priority,
        offset=args.offset,
        limit=args.target_limit,
    )

    if not args.all and not args.provider:
        print("No providers selected. Use --provider PROVIDER or --all.")
        print(f"available_targets={len(items)}")
        return 0 if args.write_registry else 2

    reports = run_targets(
        targets,
        per_target_limit=args.limit if args.limit is not None else args.per_target_limit,
        save_db=args.save_db,
        mark_stale=args.mark_stale,
        max_depth=args.max_depth,
        max_pages=args.max_pages,
        detail_limit=args.detail_limit,
        timeout=args.timeout,
        parallel_workers=max(1, args.parallel_workers),
    )
    print_table(reports)
    report_path = write_report(reports)
    print(f"\nreport={report_path}")
    return 0 if any(report.success for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
