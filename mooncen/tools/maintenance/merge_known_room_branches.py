from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from psycopg2.extras import Json


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.db_utils import get_db_cursor
from tools.maintenance.backfill_missing_branch_addresses import (
    clean_text,
    is_usable_address,
    road_address_key,
)


@dataclass(frozen=True)
class MergeRule:
    provider: str
    canonical_name: str
    address: str
    source_names: tuple[str, ...] = ()
    source_prefixes: tuple[str, ...] = ()
    branch_code: str = ""
    lat: float | None = None
    lon: float | None = None
    region_sido: str = ""
    region_sigungu: str = ""
    source_url: str = ""
    coordinate_source: str = ""


RULES: tuple[MergeRule, ...] = (
    MergeRule(
        provider="MUNI_WWW_EFMC_OR_KR_C846830E",
        canonical_name="은평종합스포츠타운",
        branch_code="EFMC01",
        address="서울특별시 은평구 진관1로 40",
        source_names=("은평종합스포츠타운",),
        lat=37.6304407253603,
        lon=126.923581252389,
        region_sido="서울특별시",
        region_sigungu="은평구",
        source_url="https://www.efmc.or.kr/fmcs/850",
        coordinate_source="OFFICIAL_EFMC_DIRECTIONS",
    ),
    MergeRule(
        provider="MUNI_WWW_GYEONGJU_GO_KR_ADA8A467",
        canonical_name="북천체육시설",
        address="경상북도 경주시 구황동 883-99",
        source_names=("북천체육시설",),
        lat=35.8425488,
        lon=129.2326538,
        region_sido="경상북도",
        region_sigungu="경주시",
        source_url=(
            "https://www.gyeongju.go.kr/reserve/sports_facilities/"
            "facility_view.jsp?item_id=T0000266&mem_id=B0000031"
        ),
        coordinate_source="GOOGLE_GEOCODING_OFFICIAL_ADDRESS",
    ),
    MergeRule(
        provider="MUNI_WWW_CHEONAN_GO_KR_C97CA6FD",
        canonical_name="성정평생학습관",
        address="충청남도 천안시 서북구 성정중4길 29",
        source_names=("301",),
        region_sido="충청남도",
        region_sigungu="천안시",
        source_url=(
            "https://www.cheonan.go.kr/women/sub01_06_01.do"
        ),
    ),
    MergeRule(
        provider="MUNI_WWW_CHEONAN_GO_KR_EA8D366B",
        canonical_name="두정평생학습관",
        address="충청남도 천안시 서북구 봉정로 339",
        source_names=("컴퓨터실",),
        region_sido="충청남도",
        region_sigungu="천안시",
        source_url=(
            "https://www.cheonan.go.kr/women/sub01_06_02.do"
        ),
    ),
    MergeRule(
        provider="MUNI_EDU_YANGYANG_GO_KR_06A9551C",
        canonical_name="양양군평생학습관",
        address="강원특별자치도 양양군 양양읍 안산1길 36",
        source_names=(
            "야간",
            "101호",
            "102호",
            "301호",
            "304호",
            "305호",
            "307호",
            "308호",
            "401호",
        ),
        region_sido="강원특별자치도",
        region_sigungu="양양군",
        source_url="https://edu.yangyang.go.kr/bbs/content.php?co_id=2sub1",
    ),
    MergeRule(
        provider="MUNI_LMS_SCHC_GO_KR_A117B76B",
        canonical_name="순천시평생학습관",
        address="전라남도 순천시 중앙로 232",
        source_prefixes=("순천시평생학습관 >",),
        lat=34.9667365,
        lon=127.4865388,
        region_sido="전라남도",
        region_sigungu="순천시",
        source_url="https://lms.schc.go.kr/lms/",
        coordinate_source="GOOGLE_GEOCODING_OFFICIAL_ADDRESS",
    ),
    MergeRule(
        provider="MUNI_LMS_SCHC_GO_KR_A117B76B",
        canonical_name="인생이모작지원센터",
        address="전라남도 순천시 서문로 7-2",
        source_names=("인생이모작지원센터",),
        lat=34.9502465,
        lon=127.4826954,
        region_sido="전라남도",
        region_sigungu="순천시",
        source_url="https://www.suncheon.go.kr/kr/talk/0022/0001/",
        coordinate_source="GOOGLE_GEOCODING_OFFICIAL_ADDRESS",
    ),
    MergeRule(
        provider="MUNI_WWW_YC_GO_KR_54558363",
        canonical_name="평생학습관",
        address="경상북도 영천시 최무선로 243",
        source_names=(
            "건강교육과정",
            "교양문화과정",
            "디지털과정",
            "시민대학",
            "야간교육과정",
            "음악교육과정",
            "창업부업과정",
            "평생학습형일자리연계지원",
        ),
        region_sido="경상북도",
        region_sigungu="영천시",
        source_url=(
            "https://www.yc.go.kr/edu/portal/contents.do?mId=0108000000"
        ),
    ),
    MergeRule(
        provider="MUNI_WWW_PYEONGTAEK_GO_KR_54DAD706",
        canonical_name="서부학습공간",
        address=(
            "경기도 평택시 안중읍 서동대로 1557, 서부복지타운 3층"
        ),
        source_names=("서부학습공간",),
        region_sido="경기도",
        region_sigungu="평택시",
        source_url=(
            "https://www.pyeongtaek.go.kr/learning/contents.do?"
            "mid=0201010000"
        ),
    ),
    MergeRule(
        provider="NATIONAL_MUSEUM_OF_MODERN_ART",
        canonical_name="어린이미술관",
        address="경기도 과천시 광명로 313",
        source_names=("과천 어린이미술관",),
        region_sido="경기도",
        region_sigungu="과천시",
        source_url="https://www.mmca.go.kr/civil/policy231204.do",
    ),
    MergeRule(
        provider="HOMEPLUS",
        canonical_name="작전점",
        branch_code="0009",
        address="인천광역시 계양구 계양대로 27",
        source_names=("작전점",),
        lat=37.5261868,
        lon=126.7212673,
        region_sido="인천광역시",
        region_sigungu="계양구",
        source_url=(
            "https://mschool.homeplus.co.kr/OperationGuide/"
            "BranchStoreDetail?reqStoreCode=0009"
        ),
        coordinate_source="NAVER_LOCAL_SEARCH_BY_ADDRESS",
    ),
    MergeRule(
        provider="LOTTE",
        canonical_name="롯데문화센터 분당점",
        branch_code="0008",
        address="경기도 성남시 분당구 황새울로200번길 45",
        source_names=(
            "롯데문화센터 성인강좌",
            "롯데문화센터 아동강좌",
        ),
        lat=37.378445,
        lon=127.1142327,
        region_sido="경기도",
        region_sigungu="성남시",
        source_url=(
            "https://culture.lotteshopping.com/community/notice/"
            "view.do?notcSeqno=527"
        ),
        coordinate_source="NAVER_LOCAL_SEARCH_BY_ADDRESS",
    ),
    MergeRule(
        provider="MUNI_WWW_GN_GO_KR_E6671160",
        canonical_name="청소년수련관",
        address="강원특별자치도 강릉시 종합운동장길 72-21",
        source_names=(
            "3층 나래실",
            "3층 누리실",
            "3층 마루실",
            "3층 아라실",
        ),
        lat=37.7721825,
        lon=128.8938062,
        region_sido="강원특별자치도",
        region_sigungu="강릉시",
        source_url=(
            "https://www.gn.go.kr/gnyouth01/contents.do?key=6067"
        ),
        coordinate_source="NAVER_LOCAL_SEARCH_BY_ADDRESS",
    ),
)


def stable_branch_code(provider: Any, name: Any) -> str:
    provider_text = clean_text(provider).upper()
    name_text = clean_text(name)
    digest = hashlib.sha1(
        "|".join((provider_text, name_text, "")).encode("utf-8")
    ).hexdigest()[:12].upper()
    slug = re.sub(r"[^A-Za-z0-9가-힣]+", "_", name_text).strip("_")[:28]
    return f"{slug}_{digest}"[:50] if slug else f"{provider_text[:32]}_{digest}"[:50]


def rule_matches_source_name(rule: MergeRule, value: Any) -> bool:
    name = clean_text(value)
    return name in rule.source_names or any(
        name.startswith(prefix) for prefix in rule.source_prefixes
    )


def validate_rules(rules: tuple[MergeRule, ...] = RULES) -> list[str]:
    errors: list[str] = []
    identities: set[tuple[str, str]] = set()
    for rule in rules:
        identity = (rule.provider, rule.canonical_name)
        if identity in identities:
            errors.append(f"duplicate canonical rule: {identity!r}")
        identities.add(identity)
        if not is_usable_address(rule.address):
            errors.append(f"unusable address: {identity!r}")
        if bool(rule.lat is None) != bool(rule.lon is None):
            errors.append(f"partial coordinates: {identity!r}")
        if rule.lat is not None and not (
            33.0 <= rule.lat <= 39.5 and 124.0 <= rule.lon <= 132.0
        ):
            errors.append(f"coordinates outside South Korea: {identity!r}")
        if not rule.source_names and not rule.source_prefixes:
            errors.append(f"rule has no source selector: {identity!r}")
    return errors


def load_provider_branches(provider: str) -> list[dict[str, Any]]:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT
                b.id::text,
                b.branch_code,
                b.name,
                b.address,
                b.lat,
                b.lon,
                b.website_url,
                COUNT(c.id) AS course_count,
                COUNT(c.id) FILTER (WHERE c.is_active) AS active_course_count
            FROM branches b
            LEFT JOIN courses c ON c.branch_id = b.id
            WHERE b.provider = %(provider)s
            GROUP BY b.id
            ORDER BY b.name, b.id
            """,
            {"provider": provider},
        )
        return [dict(row) for row in cursor.fetchall()]


def choose_target(
    rule: MergeRule,
    branches: list[dict[str, Any]],
) -> dict[str, Any] | None:
    same_name = [
        branch
        for branch in branches
        if clean_text(branch["name"]) == rule.canonical_name
    ]
    address_key = road_address_key(rule.address)
    same_address = [
        branch
        for branch in same_name
        if road_address_key(branch.get("address")) == address_key
    ]
    candidates = same_address or [
        branch for branch in same_name if clean_text(branch.get("address"))
    ] or same_name
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda row: (
            road_address_key(row.get("address")) == address_key,
            bool(clean_text(row.get("address"))),
            int(row.get("active_course_count") or 0),
            int(row.get("course_count") or 0),
        ),
    )


def selected_sources(
    rule: MergeRule,
    branches: list[dict[str, Any]],
    target_id: str | None,
) -> list[dict[str, Any]]:
    result = []
    target_address_key = road_address_key(rule.address)
    for branch in branches:
        if str(branch["id"]) == clean_text(target_id):
            continue
        source_address = clean_text(branch.get("address"))
        if (
            source_address
            and road_address_key(source_address) != target_address_key
        ):
            continue
        if rule_matches_source_name(rule, branch["name"]):
            result.append(branch)
    return result


def ensure_target(
    cursor: Any,
    rule: MergeRule,
    target: dict[str, Any] | None,
) -> str:
    branch_code = (
        clean_text(target.get("branch_code")) if target else ""
    ) or rule.branch_code or stable_branch_code(rule.provider, rule.canonical_name)
    params = {
        "id": target["id"] if target else None,
        "provider": rule.provider,
        "branch_code": branch_code,
        "name": rule.canonical_name,
        "address": rule.address,
        "website_url": rule.source_url,
        "address_source": "CURATED_OFFICIAL_LOCATION",
        "lat": rule.lat,
        "lon": rule.lon,
        "coordinate_source": rule.coordinate_source or None,
        "confidence": 100,
        "region_sido": rule.region_sido,
        "region_sigungu": rule.region_sigungu,
        "location_query": rule.source_url or rule.address,
        "basic_info": Json(
            {
                "known_room_branch_merge": {
                    "source_url": rule.source_url,
                    "checked_at": datetime.now().astimezone().isoformat(),
                }
            }
        ),
    }
    if target:
        cursor.execute(
            """
            UPDATE branches
            SET name = %(name)s,
                address = %(address)s,
                website_url = COALESCE(
                    NULLIF(%(website_url)s, ''),
                    website_url
                ),
                address_source = %(address_source)s,
                lat = COALESCE(%(lat)s, lat),
                lon = COALESCE(%(lon)s, lon),
                coordinate_source = COALESCE(
                    %(coordinate_source)s,
                    coordinate_source
                ),
                location_confidence = GREATEST(
                    COALESCE(location_confidence, 0),
                    %(confidence)s
                ),
                location_verified = TRUE,
                location_checked_at = CURRENT_TIMESTAMP,
                location_query = %(location_query)s,
                region_sido = COALESCE(
                    NULLIF(%(region_sido)s, ''),
                    region_sido
                ),
                region_sigungu = COALESCE(
                    NULLIF(%(region_sigungu)s, ''),
                    region_sigungu
                ),
                basic_info = COALESCE(basic_info, '{}'::jsonb)
                    || %(basic_info)s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %(id)s::uuid
            RETURNING id::text
            """,
            params,
        )
    else:
        cursor.execute(
            """
            INSERT INTO branches (
                provider,
                branch_code,
                name,
                address,
                website_url,
                address_source,
                lat,
                lon,
                coordinate_source,
                location_confidence,
                location_verified,
                location_checked_at,
                location_query,
                region_sido,
                region_sigungu,
                basic_info
            )
            VALUES (
                %(provider)s,
                %(branch_code)s,
                %(name)s,
                %(address)s,
                %(website_url)s,
                %(address_source)s,
                %(lat)s,
                %(lon)s,
                %(coordinate_source)s,
                %(confidence)s,
                TRUE,
                CURRENT_TIMESTAMP,
                %(location_query)s,
                %(region_sido)s,
                %(region_sigungu)s,
                %(basic_info)s
            )
            ON CONFLICT (provider, branch_code)
            DO UPDATE SET
                name = EXCLUDED.name,
                address = EXCLUDED.address,
                website_url = COALESCE(
                    NULLIF(EXCLUDED.website_url, ''),
                    branches.website_url
                ),
                address_source = EXCLUDED.address_source,
                lat = COALESCE(EXCLUDED.lat, branches.lat),
                lon = COALESCE(EXCLUDED.lon, branches.lon),
                coordinate_source = COALESCE(
                    EXCLUDED.coordinate_source,
                    branches.coordinate_source
                ),
                location_confidence = GREATEST(
                    COALESCE(branches.location_confidence, 0),
                    COALESCE(EXCLUDED.location_confidence, 0)
                ),
                location_verified = TRUE,
                location_checked_at = CURRENT_TIMESTAMP,
                location_query = EXCLUDED.location_query,
                region_sido = COALESCE(
                    NULLIF(EXCLUDED.region_sido, ''),
                    branches.region_sido
                ),
                region_sigungu = COALESCE(
                    NULLIF(EXCLUDED.region_sigungu, ''),
                    branches.region_sigungu
                ),
                basic_info = COALESCE(branches.basic_info, '{}'::jsonb)
                    || EXCLUDED.basic_info,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            params,
        )
    return str(cursor.fetchone()["id"])


def apply_rules(
    planned: list[tuple[MergeRule, dict[str, Any] | None, list[dict[str, Any]]]],
) -> tuple[int, int, int]:
    targets: set[str] = set()
    moved_courses = 0
    deleted_sources = 0
    with get_db_cursor() as cursor:
        for rule, target, sources in planned:
            target_id = ensure_target(cursor, rule, target)
            targets.add(target_id)
            source_ids = [row["id"] for row in sources]
            if source_ids:
                cursor.execute(
                    """
                    UPDATE courses
                    SET branch_id = %(target_id)s::uuid,
                        venue_address = %(address)s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE branch_id = ANY(%(source_ids)s::uuid[])
                    """,
                    {
                        "target_id": target_id,
                        "address": rule.address,
                        "source_ids": source_ids,
                    },
                )
                moved_courses += cursor.rowcount
                cursor.execute(
                    """
                    DELETE FROM branches b
                    WHERE b.id = ANY(%(source_ids)s::uuid[])
                      AND NOT EXISTS (
                          SELECT 1 FROM courses c WHERE c.branch_id = b.id
                      )
                    """,
                    {"source_ids": source_ids},
                )
                deleted_sources += cursor.rowcount

            cursor.execute(
                """
                UPDATE courses
                SET venue_address = %(address)s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE branch_id = %(target_id)s::uuid
                  AND NULLIF(btrim(venue_address), '') IS NULL
                """,
                {"target_id": target_id, "address": rule.address},
            )
    return len(targets), moved_courses, deleted_sources


def write_report(
    output_dir: Path,
    planned: list[tuple[MergeRule, dict[str, Any] | None, list[dict[str, Any]]]],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"known_room_branch_merge_{stamp}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "provider",
                "canonical_name",
                "address",
                "target_branch_id",
                "source_branches",
                "source_branch_count",
                "active_courses",
                "total_courses",
                "source_url",
            ),
        )
        writer.writeheader()
        for rule, target, sources in planned:
            writer.writerow(
                {
                    "provider": rule.provider,
                    "canonical_name": rule.canonical_name,
                    "address": rule.address,
                    "target_branch_id": target["id"] if target else "",
                    "source_branches": " | ".join(
                        clean_text(row["name"]) for row in sources
                    ),
                    "source_branch_count": len(sources),
                    "active_courses": sum(
                        int(row.get("active_course_count") or 0)
                        for row in sources
                    ),
                    "total_courses": sum(
                        int(row.get("course_count") or 0) for row in sources
                    ),
                    "source_url": rule.source_url,
                }
            )
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Merge room/category pseudo-branches into reviewed physical "
            "facilities and persist their official addresses."
        )
    )
    parser.add_argument("--provider")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "logs" / "branch_address_backfill",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors = validate_rules()
    if errors:
        for error in errors:
            print(f"rule_error={error}")
        return 1

    selected_rules = [
        rule
        for rule in RULES
        if not args.provider or rule.provider == args.provider.upper()
    ]
    planned = []
    for rule in selected_rules:
        branches = load_provider_branches(rule.provider)
        target = choose_target(rule, branches)
        sources = selected_sources(
            rule,
            branches,
            str(target["id"]) if target else None,
        )
        planned.append((rule, target, sources))

    report = write_report(args.output_dir, planned)
    print(
        f"rules={len(planned)} "
        f"source_branches={sum(len(item[2]) for item in planned)} "
        f"active_courses={sum(sum(int(row.get('active_course_count') or 0) for row in item[2]) for item in planned)} "
        f"total_courses={sum(sum(int(row.get('course_count') or 0) for row in item[2]) for item in planned)} "
        f"apply={args.apply}"
    )
    print(f"report={report}")
    for rule, target, sources in planned:
        print(
            f"rule={rule.provider}/{rule.canonical_name} "
            f"target={target['id'] if target else 'new'} "
            f"sources={len(sources)} "
            f"courses={sum(int(row.get('course_count') or 0) for row in sources)}"
        )
    if args.apply:
        targets, courses, deleted = apply_rules(planned)
        print(
            f"updated_targets={targets} moved_courses={courses} "
            f"deleted_sources={deleted}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
