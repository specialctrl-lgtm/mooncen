
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..provider_metadata import PROVIDER_DEFAULTS, favicon_url_for, provider_defaults, provider_label
from .courses import (
    CURRENT_COURSE_STATUSES,
    course_current_by_date_filter,
    course_scope_filter,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/branches",
    tags=["locations"],
)


_PUBLIC_SCOPE_KEYS = ("provider", "education", "experience")


def _active_scope_course_counts(
    db: Session,
    branch_ids: list[object],
) -> dict[str, dict[str, int]]:
    """Count active courses by the same scope predicates as the course API.

    Keep each authoritative scope predicate in its own statement.  The
    experience and education predicates are deliberately comprehensive; using
    all three of them as FILTER expressions in one aggregate produces an
    exceptionally large PostgreSQL expression tree and can exceed the API's
    statement timeout for a wide nearby search.  Separate aggregates preserve
    the exact course API taxonomy while allowing PostgreSQL to discard
    non-matching rows before grouping.
    """

    unique_branch_ids = list(dict.fromkeys(branch_ids))
    if not unique_branch_ids:
        return {}

    counts_by_branch: dict[str, dict[str, int]] = {}
    common_filters = (
        models.Course.branch_id.in_(unique_branch_ids),
        models.Course.is_active.is_(True),
        models.Course.status.in_(CURRENT_COURSE_STATUSES),
        course_current_by_date_filter(),
    )
    for scope in _PUBLIC_SCOPE_KEYS:
        statement = (
            select(
                models.Course.branch_id.label("branch_id"),
                func.count(models.Course.id).label("course_count"),
            )
            .where(*common_filters, course_scope_filter(scope))
            .group_by(models.Course.branch_id)
        )
        for row in db.execute(statement).all():
            branch_counts = counts_by_branch.setdefault(
                str(row.branch_id),
                {key: 0 for key in _PUBLIC_SCOPE_KEYS},
            )
            branch_counts[scope] = int(row.course_count or 0)

    return counts_by_branch


@router.get("/providers", response_model=List[schemas.ProviderMeta])
def get_provider_metadata(db: Session = Depends(get_db)):
    """Return provider metadata based on providers currently present in the DB."""
    try:
        sql = text("""
            WITH providers AS (
                SELECT provider FROM branches
                UNION
                SELECT provider FROM courses
            ),
            branch_counts AS (
                SELECT provider,
                       COUNT(*) AS branch_count,
                       COUNT(*) FILTER (WHERE lat IS NOT NULL AND lon IS NOT NULL) AS coordinate_count
                FROM branches
                GROUP BY provider
            ),
            course_counts AS (
                SELECT provider,
                       COUNT(*) AS course_count,
                       COUNT(*) FILTER (WHERE COALESCE(is_active, true) = true) AS active_course_count,
                       COUNT(*) FILTER (
                           WHERE COALESCE(is_active, true) = true
                             AND status = 'OPEN'
                             AND (end_date IS NULL OR end_date >= (NOW() AT TIME ZONE 'Asia/Seoul')::date)
                             AND (apply_end IS NULL OR apply_end >= (NOW() AT TIME ZONE 'Asia/Seoul')::date)
                       ) AS open_course_count
                FROM courses
                GROUP BY provider
            )
            SELECT p.provider,
                   COALESCE(b.branch_count, 0) AS branch_count,
                   COALESCE(b.coordinate_count, 0) AS coordinate_count,
                   COALESCE(c.course_count, 0) AS course_count,
                   COALESCE(c.active_course_count, 0) AS active_course_count,
                   COALESCE(c.open_course_count, 0) AS open_course_count
            FROM providers p
            LEFT JOIN branch_counts b ON b.provider = p.provider
            LEFT JOIN course_counts c ON c.provider = p.provider
            ORDER BY p.provider
        """)

        rows = db.execute(sql).fetchall()
        known_order = {provider: index for index, provider in enumerate(PROVIDER_DEFAULTS)}
        results = []
        for row in rows:
            meta = provider_defaults(row.provider)
            results.append(schemas.ProviderMeta(
                provider=row.provider,
                label=meta["label"],
                marker_label=meta["marker_label"],
                marker_color=meta["marker_color"],
                branch_count=int(row.branch_count or 0),
                coordinate_count=int(row.coordinate_count or 0),
                course_count=int(row.course_count or 0),
                active_course_count=int(row.active_course_count or 0),
                open_course_count=int(row.open_course_count or 0),
            ))

        return sorted(
            results,
            key=lambda item: (
                known_order.get(item.provider, 999),
                -item.active_course_count,
                item.label,
            ),
        )
    except Exception:
        logger.exception("Failed to load branch provider metadata")
        raise HTTPException(status_code=500, detail="Branch metadata is temporarily unavailable") from None


@router.get("/nearby", response_model=List[schemas.Branch])
def get_nearby_branches(
    lat: float = Query(..., ge=-90, le=90, description="Latitude"),
    lon: float = Query(..., ge=-180, le=180, description="Longitude"),
    radius_km: float = Query(30.0, ge=0.1, le=30.0, description="Search radius in km, max 30"),
    limit: int = Query(2000, ge=1, le=2000, description="Maximum branches to return"),
    include_empty: bool = Query(False, description="Include branches with no open courses"),
    db: Session = Depends(get_db)
):
    """Find branches within radius_km of the given lat/lon using PostGIS."""
    try:
        # Use the stored geography column so PostGIS can use idx_branches_location.
        sql = text("""
            WITH nearby_branches AS (
                SELECT b.id, b.provider, b.branch_code, b.name, b.address, b.phone, b.lat, b.lon,
                       b.website_url, b.operating_hours, b.regular_holiday, b.admission_fee,
                       b.facility_type, b.facility_category, b.facility_source, b.facility_source_sheet,
                       b.facility_service_group, b.facility_collection_category, b.region_sido, b.region_sigungu,
                       b.basic_info,
                       ST_Distance(
                           b.location,
                           ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
                       ) AS distance_m
                FROM branches b
                WHERE b.location IS NOT NULL
                  AND ST_DWithin(
                      b.location,
                      ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                      :radius_m
                  )
            ),
            course_counts AS (
                SELECT c.branch_id,
                       COUNT(*) AS course_count,
                       COUNT(*) FILTER (WHERE COALESCE(is_active, true) = true) AS active_course_count,
                       COUNT(*) FILTER (
                           WHERE COALESCE(is_active, true) = true
                             AND status = 'OPEN'
                             AND (end_date IS NULL OR end_date >= (NOW() AT TIME ZONE 'Asia/Seoul')::date)
                             AND (apply_end IS NULL OR apply_end >= (NOW() AT TIME ZONE 'Asia/Seoul')::date)
                       ) AS open_course_count
                FROM courses c
                JOIN nearby_branches nb ON nb.id = c.branch_id
                GROUP BY c.branch_id
            ),
            category_rows AS (
                SELECT c.branch_id,
                       COALESCE(NULLIF(c.collection_category, ''), NULLIF(c.domain_category, ''), '미분류') AS category,
                       COUNT(*) AS active_course_count
                FROM courses c
                JOIN nearby_branches nb ON nb.id = c.branch_id
                WHERE COALESCE(c.is_active, true) = true
                  AND c.status IN ('OPEN', 'SCHEDULED', 'DEADLINE', 'WAITING')
                  AND (c.end_date IS NULL OR c.end_date >= (NOW() AT TIME ZONE 'Asia/Seoul')::date)
                  AND (
                        c.status NOT IN ('OPEN', 'DEADLINE')
                        OR c.apply_end IS NULL
                        OR c.apply_end >= (NOW() AT TIME ZONE 'Asia/Seoul')::date
                  )
                GROUP BY c.branch_id, COALESCE(NULLIF(c.collection_category, ''), NULLIF(c.domain_category, ''), '미분류')
            ),
            category_counts AS (
                SELECT branch_id,
                       jsonb_object_agg(category, active_course_count ORDER BY active_course_count DESC, category) AS category_counts,
                       array_agg(category ORDER BY active_course_count DESC, category) AS collection_categories
                FROM category_rows
                GROUP BY branch_id
            ),
            service_group_rows AS (
                SELECT c.branch_id,
                       COALESCE(NULLIF(c.service_group, ''), '기타') AS service_group,
                       COUNT(*) AS active_course_count
                FROM courses c
                JOIN nearby_branches nb ON nb.id = c.branch_id
                WHERE COALESCE(c.is_active, true) = true
                  AND c.status IN ('OPEN', 'SCHEDULED', 'DEADLINE', 'WAITING')
                  AND (c.end_date IS NULL OR c.end_date >= (NOW() AT TIME ZONE 'Asia/Seoul')::date)
                  AND (
                        c.status NOT IN ('OPEN', 'DEADLINE')
                        OR c.apply_end IS NULL
                        OR c.apply_end >= (NOW() AT TIME ZONE 'Asia/Seoul')::date
                  )
                GROUP BY c.branch_id, COALESCE(NULLIF(c.service_group, ''), '기타')
            ),
            service_group_counts AS (
                SELECT branch_id,
                       jsonb_object_agg(service_group, active_course_count ORDER BY active_course_count DESC, service_group) AS service_group_counts,
                       array_agg(service_group ORDER BY active_course_count DESC, service_group) AS service_groups
                FROM service_group_rows
                GROUP BY branch_id
            )
            SELECT nb.id, nb.provider, nb.branch_code, nb.name, nb.address, nb.phone, nb.lat, nb.lon,
                   nb.website_url, nb.operating_hours, nb.regular_holiday, nb.admission_fee,
                   nb.facility_type, nb.facility_category, nb.facility_source, nb.facility_source_sheet,
                   nb.facility_service_group, nb.facility_collection_category, nb.region_sido, nb.region_sigungu,
                   nb.basic_info,
                   COALESCE(c.course_count, 0) AS course_count,
                   COALESCE(c.active_course_count, 0) AS active_course_count,
                   COALESCE(c.open_course_count, 0) AS open_course_count,
                   COALESCE(cc.category_counts, '{}'::jsonb) AS category_counts,
                   COALESCE(cc.collection_categories, ARRAY[]::text[]) AS collection_categories,
                   COALESCE(sgc.service_group_counts, '{}'::jsonb) AS service_group_counts,
                   COALESCE(sgc.service_groups, ARRAY[]::text[]) AS service_groups,
                   nb.distance_m
            FROM nearby_branches nb
            LEFT JOIN course_counts c ON c.branch_id = nb.id
            LEFT JOIN category_counts cc ON cc.branch_id = nb.id
            LEFT JOIN service_group_counts sgc ON sgc.branch_id = nb.id
            WHERE :include_empty
               OR COALESCE(c.open_course_count, 0) > 0
            ORDER BY nb.distance_m
            LIMIT :limit
        """)
        
        rows = db.execute(
            sql,
            {
                "lat": lat,
                "lon": lon,
                "radius_m": radius_km * 1000,
                "limit": limit,
                "include_empty": include_empty,
            },
        ).fetchall()
        
        scope_counts_by_branch = _active_scope_course_counts(
            db,
            [row.id for row in rows],
        )
        grouped = {}
        for row in rows:
            collection_categories = list(row.collection_categories or [])
            category_counts = {
                str(category): int(count or 0)
                for category, count in dict(row.category_counts or {}).items()
            }
            service_groups = list(row.service_groups or [])
            service_group_counts = {
                str(service_group): int(count or 0)
                for service_group, count in dict(row.service_group_counts or {}).items()
            }
            facility_category = (row.facility_collection_category or "").strip()
            facility_service_group = (row.facility_service_group or "").strip()
            facility_count = int(row.open_course_count or 0)
            if facility_category and facility_category not in collection_categories:
                collection_categories.insert(0, facility_category)
                category_counts.setdefault(facility_category, facility_count)
            if facility_service_group and facility_service_group not in service_groups:
                service_groups.insert(0, facility_service_group)
                service_group_counts.setdefault(facility_service_group, facility_count)
            provider_meta = provider_defaults(row.provider)
            website_url = row.website_url or provider_meta.get("website_url")
            lat_value = float(row.lat) if row.lat else None
            lon_value = float(row.lon) if row.lon else None
            group_key = (
                (row.name or "").strip(),
                round(lat_value, 5) if lat_value is not None else None,
                round(lon_value, 5) if lon_value is not None else None,
            )
            item = grouped.get(group_key)
            if not item:
                item = {
                    "id": str(row.id),
                    "branch_ids": [],
                    "providers": [],
                    "provider": row.provider,
                    "provider_label": provider_label(row.provider, row.name),
                    "branch_code": row.branch_code,
                    "name": row.name,
                    "address": row.address,
                    "phone": row.phone,
                    "lat": lat_value,
                    "lon": lon_value,
                    "course_count": 0,
                    "active_course_count": 0,
                    "open_course_count": 0,
                    "collection_categories": [],
                    "category_counts": {},
                    "service_groups": [],
                    "service_group_counts": {},
                    "scope_course_counts": {
                        "provider": 0,
                        "education": 0,
                        "experience": 0,
                    },
                    "website_url": website_url,
                    "favicon_url": favicon_url_for(website_url) if website_url else provider_meta.get("favicon_url"),
                    "operating_hours": row.operating_hours,
                    "regular_holiday": row.regular_holiday,
                    "admission_fee": row.admission_fee,
                    "facility_type": row.facility_type,
                    "facility_category": row.facility_category,
                    "facility_source": row.facility_source,
                    "facility_source_sheet": row.facility_source_sheet,
                    "facility_service_group": row.facility_service_group,
                    "facility_collection_category": row.facility_collection_category,
                    "region_sido": row.region_sido,
                    "region_sigungu": row.region_sigungu,
                    "basic_info": row.basic_info,
                    "_distance_m": float(row.distance_m or 0),
                }
                grouped[group_key] = item

            branch_id = str(row.id)
            if branch_id not in item["branch_ids"]:
                item["branch_ids"].append(branch_id)
            if row.provider and row.provider not in item["providers"]:
                item["providers"].append(row.provider)
            item["course_count"] += int(row.course_count or 0)
            item["active_course_count"] += int(row.active_course_count or 0)
            item["open_course_count"] += int(row.open_course_count or 0)
            branch_scope_counts = scope_counts_by_branch.get(branch_id, {})
            for scope in _PUBLIC_SCOPE_KEYS:
                item["scope_course_counts"][scope] += int(
                    branch_scope_counts.get(scope, 0) or 0
                )
            for category, count in category_counts.items():
                item["category_counts"][category] = item["category_counts"].get(category, 0) + int(count or 0)
            for service_group, count in service_group_counts.items():
                item["service_group_counts"][service_group] = item["service_group_counts"].get(service_group, 0) + int(count or 0)

        results = []
        for item in grouped.values():
            item["collection_categories"] = [
                category
                for category, _count in sorted(
                    item["category_counts"].items(),
                    key=lambda value: (-value[1], value[0]),
                )
            ]
            item["service_groups"] = [
                service_group
                for service_group, _count in sorted(
                    item["service_group_counts"].items(),
                    key=lambda value: (-value[1], value[0]),
                )
            ]
            item["primary_collection_category"] = item["collection_categories"][0] if item["collection_categories"] else None
            item["primary_service_group"] = item["service_groups"][0] if item["service_groups"] else None
            distance_m = item.pop("_distance_m", 0)
            results.append((distance_m, schemas.Branch(**item)))

        return [branch for _distance_m, branch in sorted(results, key=lambda value: value[0])]
    except Exception:
        logger.exception("Failed to load nearby branches")
        raise HTTPException(status_code=500, detail="Nearby branch search is temporarily unavailable") from None
