from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from service_group import infer_service_group, normalize_service_group

ROOT = Path(__file__).resolve().parents[1]
COLLECTED_TARGETS = ROOT / "config" / "collected_yaml_crawl_targets.yaml"
OPERATIONAL_TARGETS = ROOT / "config" / "crawler_targets.yaml"
QUALITY_CSV = ROOT / "logs" / "crawler_dev_reports" / "generated_612_quality_20260526_183657_site_quality.csv"
OUTPUT_DIR = ROOT / "config" / "crawl_targets"

EXTRA_STATIC_TARGETS = [
    {
        "provider": "YONGIN_LIFELONG_LEARNING",
        "name": "용인시 평생학습관",
        "branch": "용인시 평생학습관",
        "url": "https://lll.yongin.go.kr/yongin/rgEdu/list.do",
        "list_urls": [
            "https://lll.yongin.go.kr/yongin/rgEdu/list.do",
            "https://lll.yongin.go.kr/yongin/irrgEdu/list.do?gbn=1&seq=23",
            "https://llsports.yongin.go.kr/m04/1/index.asp",
        ],
        "source": "static_provider",
        "priority": 1,
        "crawler_status": "ready",
        "collection_type": "static_html",
        "known_login_required_urls": ["https://llsports.yongin.go.kr/m04/1/index.asp"],
        "notes": "용인 본관은 정기교육 rgEdu/list.do와 수시/평생교육 irrgEdu/list.do?gbn=1&seq=23 목록을 기준으로 수집한다. 스포츠센터 수강신청은 llsports.yongin.go.kr/m04/1/index.asp이나 현재 로그인 페이지로 리다이렉트된다.",
        "crawler_module": "Crawler.Crawler_YonginLifelong",
        "crawler_script": "Crawler/Crawler_YonginLifelong.py",
        "access": {"login_required": False, "selenium_required": False, "api_available": False},
    },
    {
        "provider": "ESONGPA_SPORTS_CULTURE",
        "name": "송파구 체육문화회관",
        "branch": "송파구 체육문화회관",
        "url": "https://www.esongpa.or.kr/lecture/list/20000001",
        "source": "static_provider",
        "priority": 1,
        "crawler_module": "Crawler.Crawler_EsongpaSportsCulture",
        "crawler_script": "Crawler/Crawler_EsongpaSportsCulture.py",
        "access": {"login_required": False, "selenium_required": False, "api_available": False},
    },
]


CATEGORY_FILES = {
    "문화센터": "retail_culture.yaml",
    "평생학습": "lifelong_learning.yaml",
    "공공예약": "public_reservation.yaml",
    "도서관": "library.yaml",
    "체육/스포츠": "sports_facility.yaml",
    "복지관": "welfare.yaml",
    "청소년": "youth.yaml",
    "박물관/과학관": "museum_science.yaml",
    "수목원/생태": "arboretum_ecology.yaml",
    "예술/공연": "arts_culture.yaml",
    "검토필요": "generated_review.yaml",
    "제외": "deprecated.yaml",
}

SOURCE_GROUPS = {
    "문화센터": "retail_culture",
    "평생학습": "lifelong_learning",
    "공공예약": "public_reservation",
    "도서관": "library",
    "체육/스포츠": "sports_facility",
    "복지관": "welfare",
    "청소년": "youth",
    "박물관/과학관": "museum_science",
    "수목원/생태": "arboretum_ecology",
    "예술/공연": "arts_culture",
    "검토필요": "generated_review",
    "제외": "deprecated",
}


def clean(value: Any) -> str:
    return str(value or "").strip()


MEDIA_NAME_TOKENS = (
    "\uc2e0\ubb38",
    "\uc77c\ubcf4",
    "\ub274\uc2a4",
    "\ud22c\ub370\uc774",
    "\ud0c0\uc784\uc988",
    "\uc2dc\ubbfc\uc2e0\ubb38",
)

MEDIA_DOMAIN_TOKENS = (
    "news",
    "daily",
    "ilbo",
    "press",
    "times",
    "today",
    "domin",
)

MEDIA_EXACT_DOMAINS = {
    "www.asiatoday.co.kr",
    "www.boeuni.com",
    "www.brcity.kr",
    "www.cctimes.kr",
    "www.cfnews.kr",
    "www.domin.co.kr",
    "www.ggilbo.com",
    "www.gndomin.com",
    "www.gukjenews.com",
    "www.hyundaiilbo.com",
    "www.idaegu.co.kr",
    "www.igangbuk.com",
    "www.igimpo.com",
    "www.imedialife.co.kr",
    "www.jeollailbo.com",
    "www.jjn.co.kr",
    "www.jnilbo.com",
    "www.jntoday.co.kr",
    "www.joongdo.co.kr",
    "www.kbsm.net",
    "www.kjilbo.co.kr",
    "www.kmaeil.com",
    "www.kwtotalnews.kr",
    "www.kyongbuk.co.kr",
    "www.mygoyang.com",
    "www.newsfire.co.kr",
    "www.pointe.co.kr",
    "www.seoulilbo.com",
    "www.todayan.com",
    "www.yangsanilbo.com",
    "www.yg21.co.kr",
    "www.yongin21.co.kr",
}


def is_media_target(row: dict[str, Any]) -> bool:
    url = clean(row.get("url") or row.get("list_url") or row.get("base_url")).lower()
    host = urlparse(url).netloc.lower()
    name = f"{clean(row.get('name'))} {clean(row.get('branch'))}"
    if host in MEDIA_EXACT_DOMAINS:
        return True
    if any(token in name for token in MEDIA_NAME_TOKENS):
        return True
    if not host.endswith((".go.kr", ".or.kr")) and any(token in host for token in MEDIA_DOMAIN_TOKENS):
        return True
    return False


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_quality(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8-sig", newline="") as csv_file:
        for row in csv.DictReader(csv_file):
            provider = clean(row.get("provider")).upper()
            if provider:
                rows[provider] = row
    return rows


def quality_status(row: dict[str, Any] | None) -> str:
    if not row:
        return "candidate"
    collected = int(row.get("collected") or 0)
    score = float(row.get("score") or 0)
    error_kind = clean(row.get("error_kind")).upper()
    url = clean(row.get("url")).lower()

    if "e-ncom.co.kr" in url:
        return "deprecated"
    if collected <= 0:
        if error_kind in {"SSL", "TIMEOUT", "CONNECTION", "HTTPERROR"}:
            return "blocked"
        if error_kind == "NOT_FOUND":
            return "needs_discovery"
        return "needs_parser"
    if score >= 70:
        return "ready"
    return "partial"


def collection_type(row: dict[str, Any], quality: dict[str, Any] | None) -> str:
    access = row.get("access") if isinstance(row.get("access"), dict) else {}
    if access.get("api_available") is True:
        return "ajax_api"
    if access.get("selenium_required") is True:
        return "selenium"

    url = clean(row.get("url") or row.get("list_url") or row.get("base_url")).lower()
    if url.endswith((".pdf", ".hwp", ".hwpx", ".xls", ".xlsx")):
        return "document"
    if quality:
        parser = clean(quality.get("parser"))
        error = clean(quality.get("error_kind")).upper()
        if error in {"SSL", "TIMEOUT", "CONNECTION", "HTTPERROR"}:
            return "browser_or_retry"
        if "detail" in parser or "table" in parser or "card" in parser:
            return "static_html"
    if any(token in url for token in ("ajax", "api", "json")):
        return "ajax_api"
    return "unknown"


def operator_type(row: dict[str, Any], category: str) -> str:
    provider = clean(row.get("provider")).upper()
    url = clean(row.get("url") or row.get("list_url") or row.get("base_url")).lower()
    name = f"{clean(row.get('name'))} {clean(row.get('branch'))}".lower()

    if provider in {
        "HOMEPLUS",
        "EMART",
        "LOTTE",
        "LOTTE_MART",
        "HYUNDAI_DEPT",
        "SHINSEGAE_ACADEMY",
        "ELAND_RETAIL",
        "AK_PLAZA",
        "GALLERIA",
    }:
        return "대형마트/백화점"
    if category in {"공공예약", "평생학습", "체육/스포츠"} and any(token in url for token in (".go.kr", "or.kr", "isdc.co.kr")):
        return "지자체/공공기관"
    if category == "복지관":
        return "복지기관"
    if category == "도서관":
        return "교육청/도서관"
    if category == "청소년":
        return "청소년재단"
    if category in {"박물관/과학관", "수목원/생태"}:
        return "국립/공공기관"
    if "foundation" in name or "문화재단" in name:
        return "문화재단"
    return "기타"


def infer_category(row: dict[str, Any]) -> str:
    provider = clean(row.get("provider")).upper()
    source = clean(row.get("source"))
    url = clean(row.get("url") or row.get("list_url") or row.get("base_url")).lower()
    haystack = " ".join(
        [
            provider.lower(),
            clean(row.get("name")).lower(),
            clean(row.get("branch")).lower(),
            clean(row.get("group")).lower(),
            source.lower(),
            url,
        ]
    )

    if "e-ncom.co.kr" in url or is_media_target(row):
        return "제외"
    if provider in {
        "HOMEPLUS",
        "EMART",
        "LOTTE",
        "LOTTE_MART",
        "HYUNDAI_DEPT",
        "SHINSEGAE_ACADEMY",
        "ELAND_RETAIL",
        "AK_PLAZA",
        "GALLERIA",
    }:
        return "문화센터"
    if "welfare" in source or "복지" in haystack or "silver" in haystack or "senior" in haystack:
        return "복지관"
    if "sugang_sports" in source or "sports" in haystack or "sport" in haystack or "체육" in haystack or "fmcs" in haystack:
        return "체육/스포츠"
    if "youth" in haystack or "청소년" in haystack or "snyouth" in haystack:
        return "청소년"
    if "library" in haystack or "lib" in urlparse(url).netloc or "도서관" in haystack:
        return "도서관"
    if "arboretum" in haystack or "ecology" in haystack or "biological" in haystack or "생태" in haystack or "수목원" in haystack or "생물" in haystack:
        return "수목원/생태"
    if "museum" in haystack or "science" in haystack or "과학관" in haystack or "박물관" in haystack or "미술관" in haystack:
        return "박물관/과학관"
    if "public_course_targets" in source or "reservation" in provider.lower() or "reserve" in haystack or "yeyak" in haystack:
        return "공공예약"
    if "lifelong" in haystack or "learning" in haystack or "평생" in haystack or "학습" in haystack or "edu" in urlparse(url).netloc:
        return "평생학습"
    if "culture" in haystack or "art" in haystack or "문화재단" in haystack or "문화" in haystack:
        return "예술/공연"
    if source.startswith("municipal_"):
        return "평생학습"
    return "검토필요"


def normalize_row(row: dict[str, Any], *, origin: str, quality_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    provider = clean(row.get("provider") or row.get("id")).upper()
    row_quality = row.get("last_quality") if isinstance(row.get("last_quality"), dict) else None
    quality = row_quality or quality_rows.get(provider)
    explicit_category = clean(row.get("domain_category") or row.get("collection_category"))
    category = explicit_category if explicit_category in SOURCE_GROUPS else infer_category(row)
    status = clean(row.get("crawler_status")) or clean(row.get("status"))
    if status in {"ready", "partial", "needs_parser", "needs_discovery", "blocked", "candidate", "deprecated"}:
        crawler_status = status
    elif status in {"active", "testing"}:
        crawler_status = "ready"
    elif status in {"paused", "rejected"}:
        crawler_status = "deprecated"
    else:
        crawler_status = quality_status(quality)
    if category == "제외" or is_media_target(row):
        crawler_status = "deprecated"

    url = clean(row.get("url") or row.get("list_url") or row.get("base_url"))
    service_group_policy = clean(row.get("service_group_policy")).lower()
    if service_group_policy == "locked":
        service_group = normalize_service_group(row.get("service_group"))
        if not service_group:
            raise ValueError(f"{provider}: locked service_group_policy requires service_group")
    else:
        service_group = infer_service_group(
            provider=provider,
            collection_category=category,
            domain_category=category,
            source_group=SOURCE_GROUPS[category],
            operator_type=operator_type(row, category),
            branch_name=clean(row.get("branch") or row.get("name") or provider),
            raw_url=url,
            service_group=row.get("service_group"),
        )
    normalized = {
        "provider": provider,
        "name": clean(row.get("name") or row.get("label") or provider),
        "branch": clean(row.get("branch") or row.get("name") or provider),
        "collection_category": category,
        "domain_category": category,
        "operator_type": operator_type(row, category),
        "source_group": SOURCE_GROUPS[category],
        "service_group": service_group,
        "collection_type": clean(row.get("collection_type")) or collection_type(row, quality),
        "crawler_status": crawler_status,
        "priority": int(row.get("priority") or 9),
        "url": url,
        "source": clean(row.get("source") or origin),
        "origin": origin,
    }

    for key in ("service_group_policy", "municipality_code", "municipality_full_name"):
        if clean(row.get(key)):
            normalized[key] = clean(row.get(key))

    for key in ("region", "crawler_module", "crawler_script", "base_url", "list_url", "source_url"):
        if clean(row.get(key)):
            normalized[key] = clean(row.get(key))
    if isinstance(row.get("list_urls"), list) and row["list_urls"]:
        normalized["list_urls"] = [clean(url) for url in row["list_urls"] if clean(url)]
    if isinstance(row.get("known_login_required_urls"), list) and row["known_login_required_urls"]:
        normalized["known_login_required_urls"] = [clean(url) for url in row["known_login_required_urls"] if clean(url)]
    if isinstance(row.get("access"), dict):
        normalized["access"] = row["access"]
    if isinstance(row.get("notes"), list) and row["notes"]:
        normalized["notes"] = row["notes"]
    elif clean(row.get("notes")):
        normalized["notes"] = clean(row.get("notes"))
    if quality:
        normalized["last_quality"] = {
            "collected": int(quality.get("collected") or 0),
            "score": float(quality.get("score") or 0),
            "grade": clean(quality.get("grade")),
            "parser": clean(quality.get("parser")) or "none",
            "error_kind": clean(quality.get("error_kind")),
        }
    return normalized


def load_operational_targets(path: Path) -> list[dict[str, Any]]:
    data = load_yaml(path)
    targets = data.get("targets") or []
    if not isinstance(targets, list):
        return []
    return [target for target in targets if isinstance(target, dict) and clean(target.get("provider"))]


def load_collected_targets(path: Path) -> list[dict[str, Any]]:
    data = load_yaml(path)
    targets = data.get("targets") or []
    if not isinstance(targets, list):
        return []
    return [target for target in targets if isinstance(target, dict) and clean(target.get("provider"))]


def merge_targets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (clean(row.get("provider")).upper(), clean(row.get("url")))
        if key not in merged:
            merged[key] = row
            continue
        current = merged[key]
        if current.get("origin") != "operational" and row.get("origin") == "operational":
            merged[key] = {**row, "last_quality": current.get("last_quality") or row.get("last_quality")}
        elif current.get("origin") == "collected" and row.get("origin") == "static":
            merged[key] = {**row, "last_quality": current.get("last_quality") or row.get("last_quality")}
    return sorted(merged.values(), key=lambda item: (item["priority"], item["source_group"], item["provider"], item["url"]))


def write_category_files(rows: list[dict[str, Any]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["domain_category"]].append(row)

    generated_at = datetime.now().isoformat(timespec="seconds")
    for category, filename in CATEGORY_FILES.items():
        category_rows = grouped.get(category, [])
        payload = {
            "version": 1,
            "generated_at": generated_at,
            "domain_category": category,
            "source_group": SOURCE_GROUPS[category],
            "summary": {
                "targets": len(category_rows),
                "by_status": dict(Counter(row["crawler_status"] for row in category_rows)),
                "by_collection_type": dict(Counter(row["collection_type"] for row in category_rows)),
            },
            "targets": category_rows,
        }
        (output_dir / filename).write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=140),
            encoding="utf-8",
        )

    index_payload = {
        "version": 1,
        "generated_at": generated_at,
        "summary": {
            "targets": len(rows),
            "by_category": dict(Counter(row["domain_category"] for row in rows)),
            "by_status": dict(Counter(row["crawler_status"] for row in rows)),
            "by_collection_type": dict(Counter(row["collection_type"] for row in rows)),
            "by_origin": dict(Counter(row["origin"] for row in rows)),
        },
        "files": [
            {
                "domain_category": category,
                "source_group": SOURCE_GROUPS[category],
                "file": filename,
                "targets": len(grouped.get(category, [])),
            }
            for category, filename in CATEGORY_FILES.items()
        ],
    }
    (output_dir / "index.yaml").write_text(
        yaml.safe_dump(index_payload, allow_unicode=True, sort_keys=False, width=140),
        encoding="utf-8",
    )


def write_readme(output_dir: Path) -> None:
    text = """# MoonCen Crawl Targets

이 폴더는 수집 대상을 사용자 카테고리와 운영 상태 기준으로 분리한 레지스트리입니다.

## Field Guide

| field | meaning |
| --- | --- |
| `collection_category` | DB와 API에서 사용하는 수집 카테고리입니다. `domain_category`와 같은 값으로 저장합니다. |
| `domain_category` | 사용자 화면에 노출하기 쉬운 분류입니다. |
| `operator_type` | 운영 주체입니다. 지자체/공공기관, 복지기관, 대형마트/백화점 등입니다. |
| `source_group` | 크롤러 스케줄과 운영 묶음입니다. |
| `collection_type` | 수집 방식입니다. `static_html`, `ajax_api`, `selenium`, `document`, `unknown` 등이 있습니다. |
| `crawler_status` | 운영 상태입니다. `ready`, `partial`, `needs_parser`, `needs_discovery`, `blocked`, `candidate`, `deprecated`입니다. |
| `last_quality` | 최근 10건 샘플 품질 리포트에서 가져온 결과입니다. |

## Status Flow

`candidate -> needs_discovery/needs_parser -> partial -> ready`

`ready`인 대상만 자동수집 스케줄에 넣는 것을 기본 원칙으로 둡니다.
"""
    (output_dir / "README.md").write_text(text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split MoonCen crawl targets into category YAML files.")
    parser.add_argument("--collected", type=Path, default=COLLECTED_TARGETS)
    parser.add_argument("--operational", type=Path, default=OPERATIONAL_TARGETS)
    parser.add_argument("--quality-csv", type=Path, default=QUALITY_CSV)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    quality_rows = load_quality(args.quality_csv)

    rows: list[dict[str, Any]] = []
    rows.extend(
        normalize_row(row, origin="operational", quality_rows=quality_rows)
        for row in load_operational_targets(args.operational)
    )
    rows.extend(
        normalize_row(row, origin="collected", quality_rows=quality_rows)
        for row in load_collected_targets(args.collected)
    )
    rows.extend(
        normalize_row(row, origin="static", quality_rows=quality_rows)
        for row in EXTRA_STATIC_TARGETS
    )
    merged = merge_targets(rows)

    write_category_files(merged, args.output_dir)
    write_readme(args.output_dir)

    print(f"output_dir={args.output_dir}")
    print(f"targets={len(merged)}")
    for category, count in Counter(row["domain_category"] for row in merged).most_common():
        print(f"{category}={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
