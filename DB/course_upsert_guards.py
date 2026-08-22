from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from utils.course_semantic_eligibility import guard_course_before_upsert
from utils.url_security import safe_external_http_url, sanitize_course_external_urls, sanitize_course_payload


RAW_URL_DROP_QUERY_PARAMS = {
    "mooncen_course_id",
}


def normalize_course_raw_url(value: Any) -> str:
    text = safe_external_http_url(value)
    if not text:
        return ""
    parsed = urlparse(text)
    if not parsed.query:
        return text
    query_pairs = [
        (key, item_value)
        for key, item_value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in RAW_URL_DROP_QUERY_PARAMS
    ]
    return urlunparse(parsed._replace(query=urlencode(query_pairs, doseq=True)))


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _as_date_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _clean_text(value)
    return text[:10] if text else ""


def schedule_text_from_dates(start_date: Any, end_date: Any) -> str:
    start_text = _as_date_text(start_date)
    end_text = _as_date_text(end_date)
    if start_text and end_text and start_text != end_text:
        return f"{start_text} ~ {end_text}"
    return start_text or end_text


def recover_course_schedule_raw(course: dict[str, Any]) -> str:
    schedule_raw = _clean_text(course.get("schedule_raw"))
    if schedule_raw:
        return schedule_raw

    for key in ("period", "schedule_period", "education_period", "class_period"):
        value = _clean_text(course.get(key))
        if value:
            return value

    schedule_dates = course.get("schedule_dates")
    if isinstance(schedule_dates, list) and schedule_dates:
        first = _as_date_text(schedule_dates[0])
        last = _as_date_text(schedule_dates[-1])
        recovered = schedule_text_from_dates(first, last)
        if recovered:
            return recovered

    return schedule_text_from_dates(course.get("start_date"), course.get("end_date"))


def course_missing_required_display_fields(course: dict[str, Any]) -> bool:
    title = _clean_text(course.get("title") or course.get("title_raw"))
    schedule_raw = recover_course_schedule_raw(course)
    return not title or not schedule_raw


def coalesce_provider_course_id_by_raw_url(cursor: Any, course: dict[str, Any], logger: logging.Logger | None = None) -> None:
    """Keep one provider course row per canonical raw URL.

    Business identity remains (provider, provider_course_id), but some crawlers
    can rediscover the same detail URL with a different generated id. Before the
    INSERT ... ON CONFLICT runs, reuse the existing course id for the same URL so
    the write becomes an update instead of hitting the secondary DB fingerprint
    uniqueness guard.
    """
    sanitize_course_external_urls(course)
    guard_course_before_upsert(course)
    provider = str(course.get("provider") or "").strip()
    provider_course_id = str(course.get("provider_course_id") or "").strip()
    raw_url = normalize_course_raw_url(course.get("raw_url"))
    if raw_url:
        course["raw_url"] = raw_url
    if not provider or not provider_course_id or not raw_url:
        return
    sanitize_course_payload(course)

    prefer_incoming_id = bool(course.get("prefer_incoming_provider_course_id"))
    if prefer_incoming_id:
        cursor.execute(
            "SELECT 1 FROM courses WHERE provider = %s AND provider_course_id = %s LIMIT 1",
            (provider, provider_course_id),
        )
        if cursor.fetchone():
            return

    legacy_raw_url_prefix = f"{raw_url}&mooncen_course_id="
    legacy_raw_url_query = f"{raw_url}?mooncen_course_id="
    raw_url_fingerprint = hashlib.sha256(raw_url.encode("utf-8")).hexdigest()[:16]

    cursor.execute(
        """
        SELECT provider_course_id, title, branch_id
          FROM courses
         WHERE provider = %s
           AND raw_url IS NOT NULL
           AND (
                 mooncen_raw_url_fingerprint(raw_url) = mooncen_raw_url_fingerprint(%s)
              OR starts_with(btrim(raw_url), %s)
              OR starts_with(btrim(raw_url), %s)
           )
           AND provider_course_id <> %s
         ORDER BY last_seen_at DESC NULLS LAST, updated_at DESC NULLS LAST
         LIMIT 1
        """,
        (provider, raw_url, legacy_raw_url_prefix, legacy_raw_url_query, provider_course_id),
    )
    row = cursor.fetchone()
    if not row:
        return

    existing_course_id = row["provider_course_id"] if isinstance(row, dict) else row[0]
    existing_title = str((row["title"] if isinstance(row, dict) else row[1]) or "").strip()
    existing_branch_id = str((row["branch_id"] if isinstance(row, dict) else row[2]) or "").strip()
    incoming_title = str(course.get("title") or "").strip()
    incoming_branch_id = str(course.get("branch_id") or "").strip()
    if not existing_course_id:
        return

    if logger and (existing_title != incoming_title or existing_branch_id != incoming_branch_id):
        if logger:
            logger.warning(
                "Duplicate raw_url coalesced with metadata mismatch before upsert. provider=%s raw_url_sha256=%s incoming_course_id=%s existing_course_id=%s existing_title=%s incoming_title=%s existing_branch_id=%s incoming_branch_id=%s",
                provider,
                raw_url_fingerprint,
                provider_course_id,
                existing_course_id,
                existing_title,
                incoming_title,
                existing_branch_id,
                incoming_branch_id,
            )
    elif logger:
        logger.warning(
            "Duplicate raw_url coalesced before upsert. provider=%s raw_url_sha256=%s incoming_course_id=%s existing_course_id=%s",
            provider,
            raw_url_fingerprint,
            provider_course_id,
            existing_course_id,
        )
    if prefer_incoming_id:
        cursor.execute(
            """
            UPDATE courses
               SET provider_course_id = %s,
                   updated_at = CURRENT_TIMESTAMP
             WHERE provider = %s
               AND provider_course_id = %s
            """,
            (provider_course_id, provider, existing_course_id),
        )
        return
    course["provider_course_id"] = existing_course_id


def coalesce_provider_course_ids_by_raw_url(
    cursor: Any,
    courses: list[dict[str, Any]],
    *,
    execute_values_fn: Callable[..., Any],
    logger: logging.Logger | None = None,
) -> None:
    """Set-based variant of the raw URL identity guard for staging batches."""
    for course in courses:
        sanitize_course_external_urls(course)
        guard_course_before_upsert(course)

    guard_values: list[tuple[Any, ...]] = []
    guarded_courses: dict[int, dict[str, Any]] = {}
    raw_url_owners: dict[tuple[str, str], str] = {}
    for ordinal, course in enumerate(courses):
        sanitize_course_external_urls(course)
        provider = str(course.get("provider") or "").strip()
        provider_course_id = str(course.get("provider_course_id") or "").strip()
        raw_url = normalize_course_raw_url(course.get("raw_url"))
        if raw_url:
            course["raw_url"] = raw_url
        if not provider or not provider_course_id or not raw_url:
            continue
        raw_url_key = (provider, raw_url)
        previous_course_id = raw_url_owners.get(raw_url_key)
        if previous_course_id is not None:
            raw_url_fingerprint = hashlib.sha256(
                raw_url.encode("utf-8")
            ).hexdigest()[:16]
            raise ValueError(
                "staging batch contains duplicate canonical raw_url: "
                f"provider={provider} raw_url_sha256={raw_url_fingerprint} "
                f"course_ids={previous_course_id},{provider_course_id}"
            )
        raw_url_owners[raw_url_key] = provider_course_id
        sanitize_course_payload(course)
        guarded_courses[ordinal] = course
        guard_values.append(
            (
                ordinal,
                provider,
                provider_course_id,
                raw_url,
                bool(course.get("prefer_incoming_provider_course_id")),
                str(course.get("title") or "").strip(),
                str(course.get("branch_id") or "").strip(),
            )
        )
    if not guard_values:
        return

    cursor.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS tmp_mooncen_course_identity_guard (
            ordinal INTEGER PRIMARY KEY,
            provider TEXT NOT NULL,
            provider_course_id TEXT NOT NULL,
            raw_url TEXT NOT NULL,
            prefer_incoming BOOLEAN NOT NULL,
            title TEXT NOT NULL,
            branch_id TEXT NOT NULL
        ) ON COMMIT DROP
        """
    )
    cursor.execute("TRUNCATE tmp_mooncen_course_identity_guard")
    execute_values_fn(
        cursor,
        """
        INSERT INTO tmp_mooncen_course_identity_guard (
            ordinal,
            provider,
            provider_course_id,
            raw_url,
            prefer_incoming,
            title,
            branch_id
        ) VALUES %s
        """,
        guard_values,
        page_size=min(len(guard_values), 1000),
    )
    cursor.execute(
        """
        WITH incoming_rows AS MATERIALIZED (
            SELECT
                incoming.*,
                EXISTS (
                    SELECT 1
                      FROM courses owned
                     WHERE owned.provider = incoming.provider
                       AND owned.provider_course_id = incoming.provider_course_id
                ) AS incoming_id_exists
              FROM tmp_mooncen_course_identity_guard incoming
        ),
        legacy_courses AS MATERIALIZED (
            SELECT
                existing.provider,
                existing.provider_course_id,
                existing.title,
                existing.branch_id,
                existing.last_seen_at,
                existing.updated_at,
                CASE
                    WHEN strpos(btrim(existing.raw_url), '&mooncen_course_id=') > 0
                    THEN split_part(btrim(existing.raw_url), '&mooncen_course_id=', 1)
                    WHEN strpos(btrim(existing.raw_url), '?mooncen_course_id=') > 0
                    THEN split_part(btrim(existing.raw_url), '?mooncen_course_id=', 1)
                    ELSE NULL
                END AS canonical_raw_url
              FROM courses existing
              JOIN (SELECT DISTINCT provider FROM incoming_rows) selected
                ON selected.provider = existing.provider
             WHERE existing.raw_url IS NOT NULL
               AND strpos(existing.raw_url, 'mooncen_course_id=') > 0
        ),
        candidates AS (
            SELECT
                incoming.ordinal,
                incoming.provider,
                incoming.provider_course_id AS incoming_course_id,
                existing.provider_course_id AS existing_course_id,
                incoming.title AS incoming_title,
                existing.title AS existing_title,
                incoming.branch_id AS incoming_branch_id,
                existing.branch_id AS existing_branch_id,
                incoming.prefer_incoming,
                incoming.incoming_id_exists,
                0 AS match_priority,
                existing.last_seen_at,
                existing.updated_at
              FROM incoming_rows incoming
              JOIN courses existing
                ON existing.provider = incoming.provider
               AND mooncen_raw_url_fingerprint(existing.raw_url)
                   = mooncen_raw_url_fingerprint(incoming.raw_url)
               AND existing.provider_course_id <> incoming.provider_course_id
            UNION ALL
            SELECT
                incoming.ordinal,
                incoming.provider,
                incoming.provider_course_id AS incoming_course_id,
                existing.provider_course_id AS existing_course_id,
                incoming.title AS incoming_title,
                existing.title AS existing_title,
                incoming.branch_id AS incoming_branch_id,
                existing.branch_id AS existing_branch_id,
                incoming.prefer_incoming,
                incoming.incoming_id_exists,
                1 AS match_priority,
                existing.last_seen_at,
                existing.updated_at
              FROM incoming_rows incoming
              JOIN legacy_courses existing
                ON existing.provider = incoming.provider
               AND existing.canonical_raw_url = incoming.raw_url
               AND existing.provider_course_id <> incoming.provider_course_id
        ),
        ranked AS (
            SELECT
                candidates.*,
                row_number() OVER (
                    PARTITION BY ordinal
                    ORDER BY
                        match_priority,
                        last_seen_at DESC NULLS LAST,
                        updated_at DESC NULLS LAST,
                        existing_course_id
                ) AS candidate_rank
              FROM candidates
        )
        SELECT
            ordinal,
            provider,
            incoming_course_id,
            existing_course_id,
            incoming_title,
            existing_title,
            incoming_branch_id,
            existing_branch_id,
            prefer_incoming,
            incoming_id_exists
          FROM ranked
         WHERE candidate_rank = 1
         ORDER BY ordinal
        """
    )
    candidates = cursor.fetchall() or []
    migrations: list[tuple[str, str, str]] = []
    for candidate in candidates:
        if isinstance(candidate, dict):
            values = candidate
        else:
            values = dict(
                zip(
                    (
                        "ordinal",
                        "provider",
                        "incoming_course_id",
                        "existing_course_id",
                        "incoming_title",
                        "existing_title",
                        "incoming_branch_id",
                        "existing_branch_id",
                        "prefer_incoming",
                        "incoming_id_exists",
                    ),
                    candidate,
                )
            )
        ordinal = int(values["ordinal"])
        course = guarded_courses[ordinal]
        provider = str(values["provider"])
        incoming_course_id = str(values["incoming_course_id"])
        existing_course_id = str(values["existing_course_id"])
        if logger:
            raw_url = str(course.get("raw_url") or "")
            logger.warning(
                "Duplicate raw_url coalesced before batch upsert. provider=%s raw_url_sha256=%s incoming_course_id=%s existing_course_id=%s",
                provider,
                hashlib.sha256(raw_url.encode("utf-8")).hexdigest()[:16],
                incoming_course_id,
                existing_course_id,
            )
        if bool(values["prefer_incoming"]) and not bool(
            values["incoming_id_exists"]
        ):
            migrations.append((provider, incoming_course_id, existing_course_id))
        else:
            course["provider_course_id"] = existing_course_id

    if migrations:
        execute_values_fn(
            cursor,
            """
            WITH migrations (
                provider,
                incoming_course_id,
                existing_course_id
            ) AS (VALUES %s)
            UPDATE courses existing
               SET provider_course_id = migrations.incoming_course_id,
                   updated_at = CURRENT_TIMESTAMP
              FROM migrations
             WHERE existing.provider = migrations.provider
               AND existing.provider_course_id = migrations.existing_course_id
            """,
            migrations,
            page_size=min(len(migrations), 1000),
        )


def _relation_exists(cursor: Any, relation_name: str) -> bool:
    cursor.execute("SELECT to_regclass(%s) AS relation_name", (f"public.{relation_name}",))
    row = cursor.fetchone()
    if isinstance(row, dict):
        return bool(row.get("relation_name"))
    return bool(row and row[0])


def deduplicate_course_raw_urls_for_provider(cursor: Any, provider: str, logger: logging.Logger | None = None) -> int:
    """Remove duplicate course rows that share the same provider/raw_url.

    This is a pre-migration/repair path for legacy rows. It preserves the newest
    active row for each raw URL and migrates dependent records before deletion;
    normal writes are subsequently protected by the URL fingerprint index.
    """
    provider = str(provider or "").strip()
    if not provider:
        return 0

    cursor.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS tmp_mooncen_course_raw_url_dupes (
            duplicate_id uuid PRIMARY KEY,
            survivor_id uuid NOT NULL
        ) ON COMMIT DROP
        """
    )
    cursor.execute("TRUNCATE tmp_mooncen_course_raw_url_dupes")
    cursor.execute(
        """
        INSERT INTO tmp_mooncen_course_raw_url_dupes (duplicate_id, survivor_id)
        WITH ranked AS (
            SELECT
                id,
                first_value(id) OVER (
                    PARTITION BY provider, btrim(raw_url)
                    ORDER BY
                        is_active DESC NULLS LAST,
                        last_seen_at DESC NULLS LAST,
                        updated_at DESC NULLS LAST,
                        first_seen_at DESC NULLS LAST,
                        id
                ) AS survivor_id,
                row_number() OVER (
                    PARTITION BY provider, btrim(raw_url)
                    ORDER BY
                        is_active DESC NULLS LAST,
                        last_seen_at DESC NULLS LAST,
                        updated_at DESC NULLS LAST,
                        first_seen_at DESC NULLS LAST,
                        id
                ) AS row_number
            FROM courses
            WHERE provider = %s
              AND raw_url IS NOT NULL
              AND btrim(raw_url) <> ''
        )
        SELECT id, survivor_id
          FROM ranked
         WHERE row_number > 1
        """,
        (provider,),
    )
    duplicate_count = cursor.rowcount or 0
    if duplicate_count <= 0:
        return 0

    if _relation_exists(cursor, "user_favorites"):
        cursor.execute(
            """
            INSERT INTO user_favorites (user_id, course_id)
            SELECT uf.user_id, d.survivor_id
              FROM user_favorites uf
              JOIN tmp_mooncen_course_raw_url_dupes d ON d.duplicate_id = uf.course_id
            ON CONFLICT (user_id, course_id) DO NOTHING
            """
        )
        cursor.execute(
            """
            DELETE FROM user_favorites uf
            USING tmp_mooncen_course_raw_url_dupes d
            WHERE uf.course_id = d.duplicate_id
            """
        )

    if _relation_exists(cursor, "user_course_marks"):
        cursor.execute(
            """
            INSERT INTO user_course_marks (user_id, course_id, mark_type, created_at, updated_at)
            SELECT ucm.user_id, d.survivor_id, ucm.mark_type, ucm.created_at, now()
              FROM user_course_marks ucm
              JOIN tmp_mooncen_course_raw_url_dupes d ON d.duplicate_id = ucm.course_id
            ON CONFLICT ON CONSTRAINT unique_user_course_mark DO NOTHING
            """
        )
        cursor.execute(
            """
            DELETE FROM user_course_marks ucm
            USING tmp_mooncen_course_raw_url_dupes d
            WHERE ucm.course_id = d.duplicate_id
            """
        )

    if _relation_exists(cursor, "user_course_notification_settings"):
        cursor.execute(
            """
            INSERT INTO user_course_notification_settings (
                user_id,
                course_id,
                start_alarm_enabled,
                start_alarm_minutes_before,
                registration_alarm_enabled,
                registration_alarm_minutes_before,
                created_at,
                updated_at
            )
            SELECT
                settings.user_id,
                d.survivor_id,
                settings.start_alarm_enabled,
                settings.start_alarm_minutes_before,
                settings.registration_alarm_enabled,
                settings.registration_alarm_minutes_before,
                settings.created_at,
                now()
              FROM user_course_notification_settings settings
              JOIN tmp_mooncen_course_raw_url_dupes d ON d.duplicate_id = settings.course_id
            ON CONFLICT ON CONSTRAINT unique_user_course_notification_setting DO NOTHING
            """
        )
        cursor.execute(
            """
            DELETE FROM user_course_notification_settings settings
            USING tmp_mooncen_course_raw_url_dupes d
            WHERE settings.course_id = d.duplicate_id
            """
        )

    if _relation_exists(cursor, "course_update_requests"):
        cursor.execute(
            """
            DELETE FROM course_update_requests requests
            USING tmp_mooncen_course_raw_url_dupes d
            WHERE requests.course_id = d.duplicate_id
            """
        )

    cursor.execute(
        """
        DELETE FROM courses courses
        USING tmp_mooncen_course_raw_url_dupes d
        WHERE courses.id = d.duplicate_id
        """
    )
    deleted = cursor.rowcount or 0
    if deleted and logger:
        logger.warning("Deleted %s duplicate raw_url course rows for provider=%s", deleted, provider)
    return deleted


def delete_empty_branches_for_provider(cursor: Any, provider: str, logger: logging.Logger | None = None) -> int:
    provider = str(provider or "").strip()
    if not provider:
        return 0
    cursor.execute(
        """
        DELETE FROM branches b
         WHERE b.provider = %s
           AND NOT EXISTS (
                 SELECT 1
                   FROM courses c
                  WHERE c.branch_id = b.id
                    AND c.provider = b.provider
             )
        """,
        (provider,),
    )
    deleted = cursor.rowcount or 0
    if deleted and logger:
        logger.info("Deleted %s empty branches for provider=%s", deleted, provider)
    return deleted


def repair_missing_schedule_raw(cursor: Any, provider: str | None = None, logger: logging.Logger | None = None) -> int:
    provider_filter = "AND provider = %s" if provider else ""
    params: tuple[Any, ...] = (provider,) if provider else ()
    cursor.execute(
        f"""
        UPDATE courses
           SET schedule_raw = CASE
                   WHEN start_date IS NOT NULL AND end_date IS NOT NULL AND start_date <> end_date
                   THEN to_char(start_date, 'YYYY-MM-DD') || ' ~ ' || to_char(end_date, 'YYYY-MM-DD')
                   WHEN start_date IS NOT NULL
                   THEN to_char(start_date, 'YYYY-MM-DD')
                   WHEN end_date IS NOT NULL
                   THEN to_char(end_date, 'YYYY-MM-DD')
                   ELSE schedule_raw
               END,
               updated_at = CURRENT_TIMESTAMP
         WHERE is_active = TRUE
           AND NULLIF(BTRIM(COALESCE(schedule_raw, '')), '') IS NULL
           AND (start_date IS NOT NULL OR end_date IS NOT NULL)
           {provider_filter}
        """,
        params,
    )
    repaired = cursor.rowcount or 0
    if repaired and logger:
        logger.info("Repaired %s missing schedule_raw rows for provider=%s", repaired, provider or "*")
    return repaired


def deactivate_courses_missing_required_display_fields(
    cursor: Any,
    provider: str | None = None,
    logger: logging.Logger | None = None,
) -> int:
    provider_filter = "AND provider = %s" if provider else ""
    params: tuple[Any, ...] = (provider,) if provider else ()
    cursor.execute(
        f"""
        UPDATE courses
           SET is_active = FALSE,
               removed_at = COALESCE(removed_at, CURRENT_TIMESTAMP),
               updated_at = CURRENT_TIMESTAMP
         WHERE is_active = TRUE
           AND (
                NULLIF(BTRIM(COALESCE(title, '')), '') IS NULL
             OR NULLIF(BTRIM(COALESCE(schedule_raw, '')), '') IS NULL
           )
           {provider_filter}
        """,
        params,
    )
    deactivated = cursor.rowcount or 0
    if deactivated and logger:
        logger.warning(
            "Deactivated %s active courses missing title or schedule_raw for provider=%s",
            deactivated,
            provider or "*",
        )
    return deactivated


def cleanup_invalid_display_courses_for_provider(
    cursor: Any,
    provider: str,
    logger: logging.Logger | None = None,
) -> tuple[int, int]:
    repaired = repair_missing_schedule_raw(cursor, provider, logger)
    deactivated = deactivate_courses_missing_required_display_fields(cursor, provider, logger)
    return repaired, deactivated
