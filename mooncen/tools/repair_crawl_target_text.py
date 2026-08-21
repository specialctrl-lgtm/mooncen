from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.text_quality import clean_display_text, looks_mojibake, provider_code_label, readable_text


TARGET_DIR = ROOT / "config" / "crawl_targets"
COLLECTED_FILE = ROOT / "config" / "collected_yaml_crawl_targets.yaml"
REGISTRY_FILE = ROOT / "config" / "generated_yaml_crawler_registry.yaml"

FILE_METADATA = {
    "arboretum_ecology.yaml": ("수목원/생태", "arboretum_ecology", "국립/공공기관"),
    "arts_culture.yaml": ("예술/공연", "arts_culture", "공공/문화기관"),
    "deprecated.yaml": ("제외", "deprecated", "기타"),
    "generated_review.yaml": ("검토필요", "generated_review", "기타"),
    "library.yaml": ("도서관", "library", "교육청/도서관"),
    "lifelong_learning.yaml": ("평생학습", "lifelong_learning", "지자체/공공기관"),
    "museum_science.yaml": ("박물관/과학관", "museum_science", "국립/공공기관"),
    "public_reservation.yaml": ("공공예약", "public_reservation", "지자체/공공기관"),
    "retail_culture.yaml": ("문화센터", "retail_culture", "대형마트/백화점"),
    "sports_facility.yaml": ("체육/스포츠", "sports_facility", "지자체/공공기관"),
    "welfare.yaml": ("복지관", "welfare", "복지기관"),
    "youth.yaml": ("청소년", "youth", "청소년기관"),
}

MANUAL_PROVIDER_LABELS = {
    "ANYANG_LIFELONG_LEARNING": "안양시 평생학습원",
    "ESONGPA_SPORTS_CULTURE": "송파구체육문화회관",
    "HONAM_BIOLOGICAL_RESOURCES": "국립호남권생물자원관",
    "MUNI_DOKSEODANG_SD_GO_KR_A8C20229": "성동구 통합예약 교육강좌",
    "MUNI_LLL_GEUMJEONG_GO_KR_07ABBEC3": "금정구 평생학습 포털",
    "MUNI_SJECAMPUS_COM_ECBA8A53": "세종시민대학 집현전",
    "MUNI_WWW_CHEONGDO_GO_KR_0AE7DACF": "청도군 교육예약",
    "MUNI_WWW_DANGJIN_GO_KR_56847A9C": "당진시 평생교육",
    "MUNI_WWW_GANGHWA_GO_KR_E1374F0C": "강화군 평생학습",
    "MUNI_WWW_HAMAN_GO_KR_8FBD0B4C": "함안군 통합예약",
    "MUNI_WWW_ICJG_GO_KR_A42FEEF4": "인천 중구 교육포털",
    "MUNI_WWW_JEONGSEON_GO_KR_38CBA90A": "정선군 평생교육",
    "MUNI_WWW_JINDO_GO_KR_070F7C38": "진도군 평생교육",
    "MUNI_WWW_NAMHAE_GO_KR_53DE81FD": "남해군 통합예약",
    "MUNI_WWW_ULJIN_GO_KR_3EFF1FF0": "울진군 통합예약",
    "MUNI_WWW_YEOSU_GO_KR_37E703DC": "여수시 평생교육",
    "MUNI_WWW_YEOSU_GO_KR_4500585A": "여수시 평생교육",
    "YONGIN_LIFELONG_LEARNING": "용인시 평생학습관",
}

SOURCE_GROUP_METADATA = {
    "arboretum_ecology": ("수목원/생태", "국립/공공기관"),
    "arts_culture": ("예술/공연", "공공/문화기관"),
    "library": ("도서관", "교육청/도서관"),
    "lifelong_learning": ("평생학습", "지자체/공공기관"),
    "museum_science": ("박물관/과학관", "국립/공공기관"),
    "public_reservation": ("공공예약", "지자체/공공기관"),
    "retail_culture": ("문화센터", "대형마트/백화점"),
    "sports_facility": ("체육/스포츠", "지자체/공공기관"),
    "welfare": ("복지관", "복지기관"),
    "youth": ("청소년", "청소년기관"),
}

LABEL_FIELDS = ("title", "name", "label", "full_name", "branch")


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def iter_mappings(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_mappings(child)


def candidate_score(label: str, field_index: int) -> tuple[int, int, int]:
    hangul_count = sum("가" <= char <= "힣" for char in label)
    institution_bonus = sum(
        token in label
        for token in ("평생", "교육", "학습", "도서관", "과학관", "박물관", "센터", "복지", "예약", "문화")
    )
    return (field_index, -institution_bonus, -hangul_count)


def collect_source_labels() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    by_url: dict[str, list[tuple[tuple[int, int, int], str]]] = defaultdict(list)
    by_provider: dict[str, list[tuple[tuple[int, int, int], str]]] = defaultdict(list)
    excluded = {COLLECTED_FILE.name, REGISTRY_FILE.name}

    for path in sorted((ROOT / "config").glob("*.yaml")):
        if path.name in excluded:
            continue
        try:
            data = load_yaml(path)
        except (OSError, ValueError, yaml.YAMLError):
            continue
        for row in iter_mappings(data):
            url = clean_display_text(row.get("url") or row.get("list_url") or row.get("base_url"))
            provider = clean_display_text(row.get("provider") or row.get("id")).upper()
            for field_index, field in enumerate(LABEL_FIELDS):
                label = readable_text(row.get(field))
                if not label or label.startswith(("http://", "https://")) or len(label) > 120:
                    continue
                item = (candidate_score(label, field_index), label)
                if url:
                    by_url[url].append(item)
                if provider:
                    by_provider[provider].append(item)

    def ordered(values: list[tuple[tuple[int, int, int], str]]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for _, label in sorted(values):
            if label not in seen:
                seen.add(label)
                result.append(label)
        return result

    return (
        {key: ordered(values) for key, values in by_url.items()},
        {key: ordered(values) for key, values in by_provider.items()},
    )


def load_database_labels() -> tuple[dict[str, list[str]], dict[str, str]]:
    from DB.db_utils import get_db_cursor

    labels: dict[str, list[str]] = defaultdict(list)
    regions: dict[str, str] = {}
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT b.provider,
                   b.name,
                   b.region_sido,
                   b.region_sigungu,
                   count(c.id) AS course_count
              FROM branches b
              LEFT JOIN courses c ON c.branch_id = b.id
             WHERE b.provider IS NOT NULL
             GROUP BY b.provider, b.name, b.region_sido, b.region_sigungu
             ORDER BY b.provider, count(c.id) DESC, b.name
            """
        )
        region_counts: dict[str, Counter[str]] = defaultdict(Counter)
        for row in cursor.fetchall():
            provider = clean_display_text(row.get("provider")).upper()
            label = readable_text(row.get("name"))
            if provider and label and label not in labels[provider]:
                labels[provider].append(label)
            region = " ".join(
                part
                for part in (readable_text(row.get("region_sido")), readable_text(row.get("region_sigungu")))
                if part
            )
            if provider and region:
                region_counts[provider][region] += int(row.get("course_count") or 1)
        for provider, counts in region_counts.items():
            regions[provider] = counts.most_common(1)[0][0]
    return dict(labels), regions


def best_label(
    row: dict[str, Any],
    by_url: dict[str, list[str]],
    by_provider: dict[str, list[str]],
    database_labels: dict[str, list[str]],
) -> str:
    provider = clean_display_text(row.get("provider")).upper()
    url = clean_display_text(row.get("url") or row.get("list_url") or row.get("base_url"))
    choices = [
        MANUAL_PROVIDER_LABELS.get(provider, ""),
        *(by_url.get(url) or []),
        *(by_provider.get(provider) or []),
        *(database_labels.get(provider) or []),
        provider_code_label(provider, url),
    ]
    return readable_text("", *choices)


def repair_target(
    row: dict[str, Any],
    *,
    filename: str,
    by_url: dict[str, list[str]],
    by_provider: dict[str, list[str]],
    database_labels: dict[str, list[str]],
    database_regions: dict[str, str],
) -> int:
    changed = 0
    provider = clean_display_text(row.get("provider")).upper()
    fallback = best_label(row, by_url, by_provider, database_labels)
    manual_label = MANUAL_PROVIDER_LABELS.get(provider, "")

    for field in ("name", "branch"):
        current = clean_display_text(row.get(field))
        should_replace = not current or looks_mojibake(current)
        if manual_label and current == provider_code_label(provider, row.get("url")):
            should_replace = True
        if should_replace:
            replacement = fallback
            if field == "branch":
                replacement = readable_text("", *(database_labels.get(provider) or []), fallback)
            if replacement and replacement != current:
                row[field] = replacement
                changed += 1

    region = clean_display_text(row.get("region"))
    if region and looks_mojibake(region):
        replacement = readable_text(database_regions.get(provider))
        if replacement:
            row["region"] = replacement
        else:
            row.pop("region", None)
        changed += 1

    metadata = FILE_METADATA.get(filename)
    if not metadata:
        source_group = readable_text(row.get("source_group"))
        inferred = SOURCE_GROUP_METADATA.get(source_group)
        if inferred:
            metadata = (inferred[0], source_group, inferred[1])
    if metadata:
        category, source_group, operator_type = metadata
        replacements = {
            "collection_category": category,
            "domain_category": category,
            "source_group": source_group,
            "operator_type": operator_type,
        }
        for field, replacement in replacements.items():
            current = clean_display_text(row.get(field))
            if not current or looks_mojibake(current):
                row[field] = replacement
                changed += 1

    for notes_field in ("notes", "crawler_notes"):
        notes = row.get(notes_field)
        if isinstance(notes, str) and looks_mojibake(notes):
            row.pop(notes_field, None)
            changed += 1
        elif isinstance(notes, list):
            clean_notes = [note for note in notes if readable_text(note)]
            if clean_notes != notes:
                if clean_notes:
                    row[notes_field] = clean_notes
                else:
                    row.pop(notes_field, None)
                changed += 1
    return changed


def repair_file(
    path: Path,
    *,
    by_url: dict[str, list[str]],
    by_provider: dict[str, list[str]],
    database_labels: dict[str, list[str]],
    database_regions: dict[str, str],
    apply: bool,
) -> tuple[int, int]:
    data = load_yaml(path)
    changed = 0
    metadata = FILE_METADATA.get(path.name)
    if metadata:
        category, source_group, _ = metadata
        for field, replacement in (("domain_category", category), ("source_group", source_group)):
            current = clean_display_text(data.get(field))
            if not current or looks_mojibake(current):
                data[field] = replacement
                changed += 1

    targets = data.get("targets") or []
    if not isinstance(targets, list):
        raise ValueError(f"targets must be a list: {path}")
    repaired_rows = 0
    for row in targets:
        if not isinstance(row, dict):
            continue
        row_changes = repair_target(
            row,
            filename=path.name,
            by_url=by_url,
            by_provider=by_provider,
            database_labels=database_labels,
            database_regions=database_regions,
        )
        if row_changes:
            repaired_rows += 1
            changed += row_changes

    target_rows = [row for row in targets if isinstance(row, dict)]
    summary = {
        "targets": len(target_rows),
        "by_status": dict(
            Counter(clean_display_text(row.get("crawler_status") or row.get("status")) or "unknown" for row in target_rows)
        ),
        "by_service_group": dict(
            Counter(clean_display_text(row.get("service_group")) for row in target_rows if clean_display_text(row.get("service_group")))
        ),
        "by_collection_type": dict(
            Counter(clean_display_text(row.get("collection_type")) or "unknown" for row in target_rows)
        ),
    }
    if path != COLLECTED_FILE and data.get("summary") != summary:
        data["summary"] = summary
        changed += 1

    if path == COLLECTED_FILE:
        collected_summary = {
            "targets": len(target_rows),
            "by_source": dict(
                Counter(
                    clean_display_text(row.get("source")) or "unknown"
                    for row in target_rows
                )
            ),
        }
        if data.get("summary") != collected_summary:
            data["summary"] = collected_summary
            changed += 1

    if changed and apply:
        data["generated_at"] = datetime.now().isoformat(timespec="seconds")
        path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=140),
            encoding="utf-8",
        )
    return repaired_rows, changed


def rebuild_index(*, apply: bool) -> bool:
    rows: list[tuple[str, dict[str, Any]]] = []
    files: list[dict[str, Any]] = []
    by_category: Counter[str] = Counter()
    for path in sorted(TARGET_DIR.glob("*.yaml")):
        if path.name == "index.yaml":
            continue
        data = load_yaml(path)
        target_rows = [row for row in data.get("targets") or [] if isinstance(row, dict)]
        category = FILE_METADATA.get(path.name, (readable_text(data.get("domain_category"), path.stem), "", ""))[0]
        source_group = FILE_METADATA.get(path.name, ("", readable_text(data.get("source_group")), ""))[1]
        by_category[category] += len(target_rows)
        rows.extend((path.name, row) for row in target_rows)
        files.append(
            {
                "domain_category": category,
                "source_group": source_group,
                "file": path.name,
                "targets": len(target_rows),
            }
        )

    target_rows = [row for _, row in rows]
    payload = {
        "version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "targets": len(target_rows),
            "by_category": dict(by_category),
            "by_status": dict(
                Counter(clean_display_text(row.get("crawler_status") or row.get("status")) or "unknown" for row in target_rows)
            ),
            "by_collection_type": dict(
                Counter(clean_display_text(row.get("collection_type")) or "unknown" for row in target_rows)
            ),
            "by_service_group": dict(
                Counter(clean_display_text(row.get("service_group")) for row in target_rows if clean_display_text(row.get("service_group")))
            ),
            "by_origin": dict(
                Counter(clean_display_text(row.get("origin")) or "unknown" for row in target_rows)
            ),
        },
        "files": files,
    }
    index_path = TARGET_DIR / "index.yaml"
    current = load_yaml(index_path) if index_path.exists() else {}
    comparable_current = {key: value for key, value in current.items() if key != "generated_at"}
    comparable_payload = {key: value for key, value in payload.items() if key != "generated_at"}
    changed = comparable_current != comparable_payload
    if changed and apply:
        index_path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=140),
            encoding="utf-8",
        )
    return changed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair mojibake in human-facing crawl target metadata.")
    parser.add_argument("--apply", action="store_true", help="Write repaired YAML. The default is a dry run.")
    parser.add_argument("--use-db", action="store_true", help="Use clean branch labels and regions from the configured database.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    by_url, by_provider = collect_source_labels()
    database_labels: dict[str, list[str]] = {}
    database_regions: dict[str, str] = {}
    if args.use_db:
        database_labels, database_regions = load_database_labels()

    paths = [COLLECTED_FILE, *sorted(TARGET_DIR.glob("*.yaml"))]
    total_rows = 0
    total_fields = 0
    for path in paths:
        if path.name == "index.yaml":
            continue
        repaired_rows, changed_fields = repair_file(
            path,
            by_url=by_url,
            by_provider=by_provider,
            database_labels=database_labels,
            database_regions=database_regions,
            apply=args.apply,
        )
        if changed_fields:
            print(f"{path.relative_to(ROOT)}: rows={repaired_rows} fields={changed_fields}")
            total_rows += repaired_rows
            total_fields += changed_fields

    if rebuild_index(apply=args.apply):
        print("config\\crawl_targets\\index.yaml: summary rebuilt")
        total_fields += 1

    mode = "applied" if args.apply else "dry-run"
    print(f"{mode}: rows={total_rows} fields={total_fields}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
