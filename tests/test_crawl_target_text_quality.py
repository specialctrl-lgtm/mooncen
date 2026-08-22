from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlsplit

import yaml

from Crawler.Crawler_GeneratedYamlTargets import apply_target_metadata, to_crawl_target
from tools.repair_crawl_target_text import repair_file
from utils.text_quality import looks_mojibake, readable_text


ROOT = Path(__file__).resolve().parents[1]


def iter_strings(value: Any, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from iter_strings(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_strings(child, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def test_mojibake_detection_is_specific_to_broken_display_text() -> None:
    assert looks_mojibake("??깃문??덈뮸")
    assert looks_mojibake("吏?먯껜/怨듦났湲곌?")
    assert looks_mojibake("?? ?? ????")
    assert not looks_mojibake("평생학습")
    assert not looks_mojibake("https://example.com/list?year=2026&return=/next?month=5")
    assert readable_text("??깃문??덈뮸", "평생학습") == "평생학습"


def test_active_crawl_target_registries_contain_no_mojibake() -> None:
    paths = [
        ROOT / "config" / "collected_yaml_crawl_targets.yaml",
        ROOT / "config" / "generated_yaml_crawler_registry.yaml",
        *sorted((ROOT / "config" / "crawl_targets").glob("*.yaml")),
    ]
    findings: list[str] = []
    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for key_path, value in iter_strings(data):
            if looks_mojibake(value):
                findings.append(f"{path.relative_to(ROOT)}{key_path}: {value!r}")
    assert findings == []


def test_crawl_target_urls_do_not_persist_session_or_csrf_credentials() -> None:
    paths = [
        ROOT / "config" / "collected_yaml_crawl_targets.yaml",
        ROOT / "config" / "generated_yaml_crawler_registry.yaml",
        *sorted((ROOT / "config" / "crawl_targets").glob("*.yaml")),
    ]
    forbidden_names = {"access_token", "csrftoken", "id_token", "refresh_token", "sessionid"}
    findings: list[str] = []
    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for key_path, value in iter_strings(data):
            if not value.startswith(("http://", "https://")):
                continue
            query_names = {name.lower() for name, _ in parse_qsl(urlsplit(value).query, keep_blank_values=True)}
            exposed = sorted(query_names & forbidden_names)
            if exposed:
                findings.append(f"{path.relative_to(ROOT)}{key_path}: {','.join(exposed)}")
    assert findings == []


def test_providers_with_verified_https_endpoints_never_regress_to_http() -> None:
    providers = {
        "MUNI_WWW_GP_GO_KR_FA65C3DB",
        "MUNI_WWW_DANGJIN_GO_KR_56847A9C",
        "MUNI_TBS_SEOUL_KR_78EB2B77",
        "MUNI_WWW_JANGHEUNG_GO_KR_5046AC44",
        "MUNI_YEDU_YONGSAN_GO_KR_36A48D5E",
        "MUNI_WWW_YEOSU_GO_KR_37E703DC",
        "MUNI_WWW_YEOSU_GO_KR_4500585A",
        "MUNI_WWW_YEONJE_GO_KR_73BA35A2",
    }
    paths = [
        ROOT / "config" / "generated_yaml_crawler_registry.yaml",
        *sorted((ROOT / "config" / "crawl_targets").glob("*.yaml")),
    ]
    findings: list[str] = []
    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for row in data.get("targets") or []:
            if not isinstance(row, dict) or row.get("provider") not in providers:
                continue
            if not str(row.get("url") or "").startswith("https://"):
                findings.append(f"{path.relative_to(ROOT)}:{row.get('provider')}")
    assert findings == []


def test_every_active_plain_http_target_has_a_reviewed_exception() -> None:
    policy = yaml.safe_load((ROOT / "config" / "http_crawl_target_exceptions.yaml").read_text(encoding="utf-8"))
    exceptions = policy.get("exceptions") or {}
    active_http: dict[str, str] = {}
    for path in sorted((ROOT / "config" / "crawl_targets").glob("*.yaml")):
        if path.name in {"deprecated.yaml", "index.yaml"}:
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for row in data.get("targets") or []:
            if isinstance(row, dict) and str(row.get("url") or "").startswith("http://"):
                active_http[str(row.get("provider") or "")] = str(row["url"])

    assert set(active_http) == set(exceptions)
    assert all(str(item.get("reason") or "").strip() for item in exceptions.values())
    assert policy.get("review_after")


def test_crawl_target_summaries_match_their_rows() -> None:
    target_dir = ROOT / "config" / "crawl_targets"
    total = 0
    file_counts: dict[str, int] = {}
    for path in sorted(target_dir.glob("*.yaml")):
        if path.name == "index.yaml":
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rows = [row for row in data.get("targets") or [] if isinstance(row, dict)]
        summary = data.get("summary") or {}
        total += len(rows)
        file_counts[path.name] = len(rows)
        assert summary.get("targets") == len(rows), path
        assert summary.get("by_status") == dict(
            Counter(str(row.get("crawler_status") or row.get("status") or "unknown").strip() for row in rows)
        ), path
        assert summary.get("by_collection_type") == dict(
            Counter(str(row.get("collection_type") or "unknown").strip() for row in rows)
        ), path

    index = yaml.safe_load((target_dir / "index.yaml").read_text(encoding="utf-8")) or {}
    assert (index.get("summary") or {}).get("targets") == total
    assert {row["file"]: row["targets"] for row in index.get("files") or []} == file_counts


def test_human_facing_docs_contain_no_replacement_damage() -> None:
    findings: list[str] = []
    for path in sorted((ROOT / "docs").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        # This operations note intentionally documents the literal damaged value.
        inspected = text.replace("`???`", "")
        if "\ufffd" in inspected or "???" in inspected:
            findings.append(str(path.relative_to(ROOT)))
    assert findings == []


def test_generated_target_runtime_replaces_broken_display_metadata() -> None:
    item = {
        "provider": "MUNI_EXAMPLE_GO_KR_12345678",
        "name": "??깃문??덈뮸",
        "branch": "吏?먯껜/怨듦났湲곌?",
        "region": "?? ?? ????",
        "url": "https://edu.example.go.kr/courses",
        "source": "test",
        "domain_category": "怨듦났?덉빟",
        "operator_type": "吏?먯껜/怨듦났湲곌?",
    }
    target = to_crawl_target(item)
    assert target.name == "Example 교육"
    assert target.branch == "Example 교육"
    assert target.region == ""

    rows = [{}]
    apply_target_metadata(rows, target)
    assert "domain_category" not in rows[0]
    assert "operator_type" not in rows[0]
    assert rows[0]["service_group"]


def test_repair_file_uses_trusted_url_label_and_file_metadata(tmp_path: Path) -> None:
    path = tmp_path / "lifelong_learning.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "domain_category": "??깃문??덈뮸",
                "targets": [
                    {
                        "provider": "MUNI_EXAMPLE_GO_KR_12345678",
                        "name": "??깃문??덈뮸",
                        "branch": "吏?먯껜/怨듦났湲곌?",
                        "region": "?? ?? ????",
                        "url": "https://edu.example.go.kr/courses",
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    repaired_rows, changed_fields = repair_file(
        path,
        by_url={"https://edu.example.go.kr/courses": ["예시시 평생학습관"]},
        by_provider={},
        database_labels={},
        database_regions={"MUNI_EXAMPLE_GO_KR_12345678": "예시도 예시시"},
        apply=True,
    )
    repaired = yaml.safe_load(path.read_text(encoding="utf-8"))
    row = repaired["targets"][0]

    assert repaired_rows == 1
    assert changed_fields >= 7
    assert repaired["domain_category"] == "평생학습"
    assert row["name"] == "예시시 평생학습관"
    assert row["branch"] == "예시시 평생학습관"
    assert row["region"] == "예시도 예시시"
    assert row["operator_type"] == "지자체/공공기관"
