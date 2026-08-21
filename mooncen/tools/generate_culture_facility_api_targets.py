from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, unquote

import requests
import yaml
from defusedxml import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "culture_facility_api_targets.yaml"
API_DOC_URL = "https://www.culture.go.kr/local/gdnc/openApiView.do"

ENDPOINTS = {
    "NATIONAL_LIBRARY": {
        "label": "국립도서관",
        "category": "national_library",
        "url": "https://apis.data.go.kr/B553457/rgnCltrFcltExmnv1/clifNtnLbrryv1",
    },
    "PUBLIC_LIBRARY": {
        "label": "공공도서관",
        "category": "public_library",
        "url": "https://apis.data.go.kr/B553457/rgnCltrFcltExmnv1/clifLbrryv1",
    },
    "MUSEUM": {
        "label": "박물관",
        "category": "museum",
        "url": "https://apis.data.go.kr/B553457/rgnCltrFcltExmnv1/clifMsmv1",
    },
    "ART_GALLERY": {
        "label": "미술관",
        "category": "art_museum",
        "url": "https://apis.data.go.kr/B553457/rgnCltrFcltExmnv1/clifArglv1",
    },
    "LIVING_CULTURE_CENTER": {
        "label": "생활문화센터",
        "category": "living_culture_center",
        "url": "https://apis.data.go.kr/B553457/rgnCltrFcltExmnv1/clifLvclCntrv1",
    },
    "CULTURE_ARTS_CENTER": {
        "label": "문예회관",
        "category": "culture_arts_center",
        "url": "https://apis.data.go.kr/B553457/rgnCltrFcltExmnv1/clifClcnv1",
    },
    "LOCAL_CULTURE_CENTER": {
        "label": "지방문화원",
        "category": "local_culture_center",
        "url": "https://apis.data.go.kr/B553457/rgnCltrFcltExmnv1/clifLclcv1",
    },
    "CULTURE_HOUSE": {
        "label": "문화의집",
        "category": "culture_house",
        "url": "https://apis.data.go.kr/B553457/rgnCltrFcltExmnv1/clifClhsv1",
    },
    "LOCAL_CULTURE_FOUNDATION": {
        "label": "지역문화재단",
        "category": "local_culture_foundation",
        "url": "https://apis.data.go.kr/B553457/rgnCltrFcltExmnv1/clifLcclFndtv1",
    },
    "LITERATURE_MUSEUM": {
        "label": "문학관",
        "category": "literature_museum",
        "url": "https://apis.data.go.kr/B553457/rgnCltrFcltExmnv1/clifLtrm1",
    },
}

QUERY_INTENTS = [
    {"id": "course", "label": "강좌", "keywords": ["강좌", "강좌 신청", "수강 신청"]},
    {"id": "education", "label": "교육", "keywords": ["교육 프로그램", "교육 신청", "어린이 교육"]},
    {"id": "experience", "label": "체험", "keywords": ["체험 프로그램", "체험 신청", "주말 체험"]},
    {"id": "guided_tour", "label": "해설", "keywords": ["전시 해설", "해설 예약", "도슨트"]},
    {"id": "reservation", "label": "예약", "keywords": ["프로그램 예약", "예약 신청", "관람 예약"]},
]

FIELD_ALIASES = {
    "name": (
        "fcltyNm",
        "fcltNm",
        "facltNm",
        "facilityNm",
        "instNm",
        "name",
        "title",
        "cltrFcltNm",
        "fcltyName",
        "시설명",
    ),
    "address": (
        "rdnmadr",
        "roadNmAddr",
        "roadAdres",
        "adres",
        "addr",
        "address",
        "laddr",
        "lnmadr",
        "rnAdres",
        "fcltyAddr",
        "주소",
    ),
    "phone": (
        "phoneNumber",
        "telNo",
        "tel",
        "phone",
        "operPhoneNumber",
        "contact",
        "fcltyTelno",
        "전화번호",
    ),
    "lat": ("latitude", "lat", "la", "y", "lcLa", "위도"),
    "lon": ("longitude", "lon", "lng", "lo", "x", "lcLo", "경도"),
    "homepage": (
        "homepageUrl",
        "hmpgUrl",
        "url",
        "homepage",
        "webUrl",
        "siteUrl",
        "fcltyUrl",
        "홈페이지",
    ),
    "sido": ("ctprvnNm", "sido", "sidoNm", "시도명"),
    "sigungu": ("signguNm", "sigungu", "sigunguNm", "시군구명"),
    "code": ("fcltyCd", "fcltCd", "facilityCode", "id", "code", "데이터기준일자"),
}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def service_key_from_env() -> str | None:
    for key in ("CULTURE_API_SERVICE_KEY", "PUBLIC_DATA_SERVICE_KEY", "DATA_GO_KR_SERVICE_KEY"):
        value = os.environ.get(key)
        if value:
            return value
    return None


def google_url(query: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(query)


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def normalize_number(value: Any) -> float | None:
    text = clean_text(value)
    if not text:
        return None
    text = text.replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def pick(row: dict[str, Any], field: str) -> Any:
    normalized = {str(key).lower(): value for key, value in row.items()}
    for alias in FIELD_ALIASES[field]:
        if alias in row and row[alias] not in (None, ""):
            return row[alias]
        lower = alias.lower()
        if lower in normalized and normalized[lower] not in (None, ""):
            return normalized[lower]
    return None


def normalize_row(provider: str, meta: dict[str, str], row: dict[str, Any], index: int) -> dict[str, Any]:
    name = clean_text(pick(row, "name"))
    code = clean_text(pick(row, "code")) or hashlib_code(provider, name or json.dumps(row, ensure_ascii=False), index)
    address = clean_text(pick(row, "address"))
    phone = clean_text(pick(row, "phone"))
    homepage = clean_text(pick(row, "homepage"))
    lat = normalize_number(pick(row, "lat"))
    lon = normalize_number(pick(row, "lon"))
    sido = clean_text(pick(row, "sido"))
    sigungu = clean_text(pick(row, "sigungu"))

    queries = build_queries(name or f"{meta['label']} {code}", meta["label"])
    return {
        "provider": f"CULTURE_API_{provider}",
        "provider_facility_id": code,
        "name": name,
        "category": meta["category"],
        "facility_type": meta["label"],
        "owner_type": "unknown",
        "region": " ".join(part for part in [sido, sigungu] if part) or "unknown",
        "address": address,
        "phone": phone,
        "lat": lat,
        "lon": lon,
        "official_url": homepage,
        "priority": 2,
        "status": "api_seed",
        "source": "culture_go_kr_local_openapi",
        "source_url": meta["url"],
        "api_doc_url": API_DOC_URL,
        "search_query_count": len(queries),
        "queries": queries,
        "api_raw_keys": sorted(str(key) for key in row.keys()),
    }


def hashlib_code(provider: str, value: str, index: int) -> str:
    import hashlib

    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:10].upper()
    return f"{provider}_{index:06d}_{digest}"


def build_queries(name: str, facility_type: str) -> list[dict[str, str]]:
    queries: list[dict[str, str]] = []
    seen: set[str] = set()
    for intent in QUERY_INTENTS:
        for keyword in intent["keywords"]:
            query = f"{name} {keyword}"
            if query in seen:
                continue
            seen.add(query)
            queries.append(
                {
                    "intent": intent["id"],
                    "intent_label": intent["label"],
                    "keyword": keyword,
                    "query": query,
                    "google_search_url": google_url(query),
                }
            )
    scoped_query = f"{name} {facility_type} 프로그램"
    if scoped_query not in seen:
        queries.append(
            {
                "intent": "facility_type",
                "intent_label": "시설유형",
                "keyword": f"{facility_type} 프로그램",
                "query": scoped_query,
                "google_search_url": google_url(scoped_query),
            }
        )
    return queries


def parse_json_items(data: Any) -> tuple[list[dict[str, Any]], int | None]:
    response = data.get("response", data) if isinstance(data, dict) else {}
    body = response.get("body", response) if isinstance(response, dict) else {}
    total = body.get("totalCount") or body.get("total_count")
    items = body.get("items", body.get("item", [])) if isinstance(body, dict) else []
    if isinstance(items, dict):
        items = items.get("item", items.get("items", []))
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        items = []
    return [item for item in items if isinstance(item, dict)], int(total) if str(total or "").isdigit() else None


def parse_xml_items(text: str) -> tuple[list[dict[str, Any]], int | None]:
    root = ET.fromstring(text)
    total_text = root.findtext(".//totalCount")
    total = int(total_text) if total_text and total_text.isdigit() else None
    rows: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        row = {child.tag: (child.text or "").strip() for child in item}
        if row:
            rows.append(row)
    return rows, total


def parse_response(response: requests.Response) -> tuple[list[dict[str, Any]], int | None]:
    text = response.text.strip()
    if not text:
        return [], None
    try:
        return parse_json_items(response.json())
    except ValueError:
        return parse_xml_items(text)


def fetch_page(session: requests.Session, endpoint: str, service_key: str, page_no: int, num_rows: int) -> tuple[list[dict[str, Any]], int | None]:
    params = {"pageNo": page_no, "numOfRows": num_rows, "_type": "json"}
    try:
        response = session.get(endpoint, params={"serviceKey": unquote(service_key), **params}, timeout=30)
        if response.status_code == 401:
            raise RuntimeError("Culture API authentication failed; verify the service-key configuration")
        response.raise_for_status()
    except RuntimeError:
        raise
    except requests.RequestException as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        status_text = f" status={status_code}" if status_code is not None else ""
        raise RuntimeError(f"Culture API request failed type={type(exc).__name__}{status_text}") from None
    return parse_response(response)


def collect_provider(session: requests.Session, provider: str, service_key: str, limit: int, num_rows: int, max_pages: int) -> tuple[list[dict], dict]:
    meta = ENDPOINTS[provider]
    rows: list[dict] = []
    total_count: int | None = None
    page = 1
    while True:
        items, total = fetch_page(session, meta["url"], service_key, page, num_rows)
        if total is not None:
            total_count = total
        if not items:
            break
        for item in items:
            rows.append(normalize_row(provider, meta, item, len(rows) + 1))
            if limit and len(rows) >= limit:
                break
        if limit and len(rows) >= limit:
            break
        if total_count is not None and len(rows) >= total_count:
            break
        if max_pages and page >= max_pages:
            break
        page += 1
    return rows, {"provider": provider, "label": meta["label"], "collected": len(rows), "total_count": total_count, "endpoint": meta["url"]}


def build_yaml(targets: list[dict], provider_summaries: list[dict]) -> dict:
    return {
        "version": 1,
        "generated_at": date.today().isoformat(),
        "source": {
            "name": "지역문화통합정보시스템 OpenAPI",
            "url": API_DOC_URL,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
        "scope": "문화포털 지역문화통합정보시스템 OpenAPI 기반 문화시설 위치/연락처/홈페이지 및 강좌·교육·체험 검색 시드",
        "collection_policy": {
            "include": [
                "API가 제공하는 문화시설 기본정보를 공식 시설 후보로 사용한다.",
                "시설명 기반으로 강좌, 교육, 체험, 해설, 예약 검색 시드를 생성한다.",
                "주소/좌표가 있는 경우 기존 크롤러 지점 보강 또는 신규 지점 후보로 사용한다.",
            ],
            "exclude": [
                "API 시설 기본정보만으로 강좌가 있다고 확정하지 않는다.",
                "검색 결과에서 블로그, 뉴스, 여행 후기, 위키 문서는 후보에서 제외한다.",
            ],
            "preferred_domains": [".go.kr", ".or.kr", ".re.kr", "official facility domains"],
        },
        "query_intents": QUERY_INTENTS,
        "targets": targets,
        "summary": {
            "target_count": len(targets),
            "query_count": sum(len(target["queries"]) for target in targets),
            "provider_count": len(provider_summaries),
            "providers": provider_summaries,
            "categories": sorted({target["category"] for target in targets}),
        },
    }


def parse_providers(values: list[str]) -> list[str]:
    if not values or any(value.upper() == "ALL" for value in values):
        return list(ENDPOINTS)
    providers = []
    for value in values:
        provider = value.upper()
        if provider not in ENDPOINTS:
            raise SystemExit(f"Unknown provider: {value}. choices={', '.join(ENDPOINTS)}")
        providers.append(provider)
    return providers


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Generate culture facility search targets from culture.go.kr local OpenAPI.")
    parser.add_argument("--provider", action="append", default=[], help="Provider code. Repeatable. Use ALL for all endpoints.")
    parser.add_argument("--service-key", default=None, help="Public data service key. Defaults to CULTURE_API_SERVICE_KEY/PUBLIC_DATA_SERVICE_KEY/DATA_GO_KR_SERVICE_KEY.")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--limit", type=int, default=0, help="Max rows per provider. 0 means all available rows.")
    parser.add_argument("--num-rows", type=int, default=100, help="API numOfRows per request.")
    parser.add_argument("--max-pages", type=int, default=0, help="Max pages per provider. 0 means until exhausted.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and summarize without writing YAML.")
    args = parser.parse_args()

    service_key = args.service_key or service_key_from_env()
    if not service_key:
        raise SystemExit("Missing service key. Set CULTURE_API_SERVICE_KEY in .env or pass --service-key.")

    session = requests.Session()
    targets: list[dict] = []
    summaries: list[dict] = []
    for provider in parse_providers(args.provider):
        rows, summary = collect_provider(session, provider, service_key, args.limit, args.num_rows, args.max_pages)
        targets.extend(row for row in rows if row.get("name"))
        summaries.append(summary)
        print(f"{provider:24s} collected={summary['collected']} total={summary['total_count']}")

    data = build_yaml(targets, summaries)
    print(f"summary targets={data['summary']['target_count']} queries={data['summary']['query_count']}")
    if not args.dry_run:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=140), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
