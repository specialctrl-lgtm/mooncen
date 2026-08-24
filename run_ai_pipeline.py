import argparse
import json
import os
import socket
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, time as datetime_time, timedelta
from typing import Dict, Iterator, List, Optional, Sequence

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from DB.db_utils import get_db_cursor
from ai_processor import AIProcessor, logger

RUNNING = True
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
PID_FILE = os.path.join(LOG_DIR, "ai_worker.pid")
AI_NODE_METRICS_FILE = os.path.join(LOG_DIR, "ai_node_metrics.jsonl")


def handle_shutdown(signum, _frame):
    global RUNNING
    logger.info("Received signal %s. Stopping AI worker...", signum)
    RUNNING = False


def parse_clock(value: str) -> datetime_time:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("time must be HH:MM") from exc

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise argparse.ArgumentTypeError("time must be HH:MM in 00:00-23:59")
    return datetime_time(hour=hour, minute=minute)


def is_within_active_window(now: datetime_time, start: datetime_time, end: datetime_time) -> bool:
    if start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end


def is_within_ai_active_schedule(
    now: datetime,
    start: datetime_time,
    end: datetime_time,
    weekend_24h: bool = False,
) -> bool:
    if weekend_24h and now.weekday() >= 5:
        return True
    return is_within_active_window(now.time(), start, end)


def seconds_until_active_window(now: datetime, start: datetime_time, end: datetime_time) -> int:
    if is_within_active_window(now.time(), start, end):
        return 0

    start_today = now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    target = start_today if now < start_today else start_today + timedelta(days=1)
    return max(1, int((target - now).total_seconds()))


def seconds_until_ai_active_schedule(
    now: datetime,
    start: datetime_time,
    end: datetime_time,
    weekend_24h: bool = False,
) -> int:
    if is_within_ai_active_schedule(now, start, end, weekend_24h):
        return 0

    candidates = []
    for day_offset in range(0, 8):
        day = (now + timedelta(days=day_offset)).date()
        active_start = datetime.combine(day, start).replace(second=0, microsecond=0)
        if active_start > now:
            candidates.append(active_start)
        if weekend_24h and day.weekday() == 5:
            weekend_start = datetime.combine(day, datetime_time(0, 0))
            if weekend_start > now:
                candidates.append(weekend_start)

    if not candidates:
        return seconds_until_active_window(now, start, end)
    return max(1, int((min(candidates) - now).total_seconds()))


def active_schedule_label(start: datetime_time, end: datetime_time, weekend_24h: bool) -> str:
    label = f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"
    if weekend_24h:
        label += " + weekend 24h"
    return label


def env_flag(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int, minimum: int = 1, maximum: int = 16) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value.strip())
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def ollama_worker_hosts() -> list[str]:
    raw_hosts = os.getenv("OLLAMA_HOSTS", "").strip()
    hosts = [item.strip() for item in raw_hosts.replace(";", ",").split(",") if item.strip()]
    if not hosts:
        hosts = [os.getenv("OLLAMA_URL") or os.getenv("OLLAMA_HOST") or "http://wtr-linux:11434"]
    normalized: list[str] = []
    seen: set[str] = set()
    for host in hosts:
        value = host.rstrip("/")
        if not value:
            continue
        if not value.startswith(("http://", "https://")):
            value = f"http://{value}"
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(value)
    return normalized or ["http://wtr-linux:11434"]


def record_ai_node_metric(processor: AIProcessor, course_id: str, task: str, success: bool) -> None:
    metrics = dict(getattr(processor, "last_call_metrics", {}) or {})
    if not metrics:
        return
    eval_count = int(metrics.get("eval_count") or 0)
    eval_duration_ns = int(metrics.get("eval_duration_ns") or 0)
    elapsed_seconds = float(metrics.get("elapsed_seconds") or 0.0)
    output_tokens_per_second = (
        round(eval_count / (eval_duration_ns / 1_000_000_000), 2)
        if eval_count > 0 and eval_duration_ns > 0
        else None
    )
    row = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "task": task,
        "course_id": course_id,
        "success": bool(success),
        "host": metrics.get("host"),
        "model": metrics.get("model"),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "eval_count": eval_count,
        "eval_duration_ns": eval_duration_ns,
        "prompt_eval_count": metrics.get("prompt_eval_count"),
        "total_duration_ns": metrics.get("total_duration_ns"),
        "output_tokens_per_second": output_tokens_per_second,
    }
    try:
        ensure_log_dir()
        with open(AI_NODE_METRICS_FILE, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        logger.warning("Skipping AI node metric write: %s", exc)


def normalize_title_for_compare(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_scalar_for_compare(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def title_ai_changed(course: dict, title_result: dict) -> bool:
    current_title = normalize_title_for_compare(course.get("title"))
    clean_title = normalize_title_for_compare(title_result.get("title"))
    if clean_title and clean_title != current_title:
        return True

    comparable_fields = (
        ("target", "target"),
        ("target_age_group", "target_age_group"),
        ("target_min_age", "target_min_age"),
        ("target_max_age", "target_max_age"),
    )
    for course_key, result_key in comparable_fields:
        result_value = title_result.get(result_key)
        if result_value is None or result_value == "":
            continue
        if normalize_scalar_for_compare(course.get(course_key)) != normalize_scalar_for_compare(result_value):
            return True

    if title_result.get("target_with_parent") and not course.get("target_with_parent"):
        return True

    if title_result.get("clear_target_age_bounds") and (
        course.get("target_min_age") is not None or course.get("target_max_age") is not None
    ):
        return True

    if title_result.get("clear_target_text") and normalize_scalar_for_compare(course.get("target")):
        return True

    result_tags = [str(tag).strip() for tag in (title_result.get("target_tags") or []) if str(tag).strip()]
    course_tags = [str(tag).strip() for tag in (course.get("target_tags") or []) if str(tag).strip()]
    if result_tags and not course_tags:
        return True

    return False


def course_needs_ai_work(
    course: dict,
    process_title: bool = True,
    process_summary: bool = True,
) -> bool:
    if process_summary and not course.get("is_ai_processed"):
        return True
    if process_title and not course.get("ai_title_processed"):
        source = str((course.get("ai_title_result") or {}).get("source") or "")
        return source != "title_unchanged"
    return False


@contextmanager
def course_ai_lock(course_id: str) -> Iterator[bool]:
    with get_db_cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(772001, hashtext(%s)) AS locked", (course_id,))
        row = cursor.fetchone()
        locked = bool(row and row.get("locked"))
        if not locked:
            yield False
            return
        try:
            yield True
        finally:
            cursor.execute("SELECT pg_advisory_unlock(772001, hashtext(%s))", (course_id,))


def fetch_course_for_ai(course_id: str) -> Optional[dict]:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, provider, title, title_raw, title_prefix_removed, target,
                   target_age_group, target_min_age, target_max_age,
                   target_with_parent, target_tags, target_age_is_explicit,
                   description, category_raw, schedule_raw,
                   COALESCE(is_ai_processed, FALSE) AS is_ai_processed,
                   COALESCE(ai_title_processed, FALSE) AS ai_title_processed,
                   COALESCE(ai_title_result, '{}'::jsonb) AS ai_title_result
            FROM courses
            WHERE id = CAST(%s AS uuid)
              AND COALESCE(is_active, TRUE) = TRUE
            """,
            (course_id,),
        )
        return cursor.fetchone()


def ensure_log_dir() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)


def is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False

    if os.name != "nt":
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as cmdline_file:
                cmdline = cmdline_file.read().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
            return "run_ai_pipeline.py" in cmdline
        except OSError:
            return False

    return True


def read_pid_file() -> Optional[int]:
    if not os.path.exists(PID_FILE):
        return None

    try:
        with open(PID_FILE, "r", encoding="utf-8") as pid_file:
            return int(pid_file.read().strip())
    except (OSError, ValueError):
        return None


def acquire_worker_lock() -> bool:
    ensure_log_dir()
    existing_pid = read_pid_file()

    if existing_pid and is_process_running(existing_pid):
        logger.error("AI worker is already running with PID %s.", existing_pid)
        return False

    if existing_pid:
        try:
            os.remove(PID_FILE)
            logger.warning("Removed stale PID file for PID %s.", existing_pid)
        except OSError as exc:
            logger.error("Failed to remove stale PID file: %s", exc)
            return False

    try:
        with open(PID_FILE, "w", encoding="utf-8") as pid_file:
            pid_file.write(str(os.getpid()))
    except OSError as exc:
        logger.error("Failed to create PID file: %s", exc)
        return False

    logger.info("Worker lock acquired. PID=%s", os.getpid())
    return True


def release_worker_lock() -> None:
    existing_pid = read_pid_file()
    if existing_pid != os.getpid():
        return

    try:
        os.remove(PID_FILE)
        logger.info("Worker lock released.")
    except OSError as exc:
        logger.warning("Failed to remove PID file: %s", exc)


def fetch_unprocessed_courses(
    limit: int = 10,
    process_title: bool = True,
    process_summary: bool = True,
    providers: Optional[Sequence[str]] = None,
) -> List[dict]:
    conditions = []
    if process_summary:
        conditions.append("COALESCE(is_ai_processed, FALSE) = FALSE")
    if process_title:
        conditions.append(
            """
            COALESCE(ai_title_processed, FALSE) = FALSE
            AND COALESCE(ai_title_result->>'source', '') <> 'title_unchanged'
            """
        )
    where_clause = " OR ".join(conditions) or "FALSE"
    params: list[object] = []
    if providers:
        where_clause = f"({where_clause}) AND provider = ANY(%s)"
        params.append([provider.upper() for provider in providers])
    params.append(limit)

    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, provider, title, title_raw, title_prefix_removed, target,
                   target_age_group, target_min_age, target_max_age,
                   target_with_parent, target_tags, target_age_is_explicit,
                   description, category_raw, schedule_raw,
                   COALESCE(is_ai_processed, FALSE) AS is_ai_processed,
                   COALESCE(ai_title_processed, FALSE) AS ai_title_processed
            FROM courses
            WHERE ({where_clause})
              AND COALESCE(is_active, TRUE) = TRUE
            ORDER BY updated_at NULLS FIRST, created_at NULLS FIRST, id
            LIMIT %s
            """,
            params,
        )
        return cursor.fetchall()


def reset_ai_processing(
    process_title: bool = True,
    process_summary: bool = True,
    providers: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    restore_title_from_raw: bool = True,
    reset_target_fields: bool = False,
    dry_run: bool = False,
) -> int:
    where_parts = ["TRUE"]
    params: list[object] = []
    if providers:
        where_parts.append("provider = ANY(%s)")
        params.append([provider.upper() for provider in providers])
    where_clause = " AND ".join(where_parts)
    limit_clause = "LIMIT %s" if limit else ""
    if limit:
        params.append(limit)

    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            WITH target_rows AS (
                SELECT id
                FROM courses
                WHERE {where_clause}
                ORDER BY updated_at NULLS FIRST, created_at NULLS FIRST, id
                {limit_clause}
            )
            SELECT COUNT(*) AS count
            FROM target_rows
            """,
            params,
        )
        target_count = int(cursor.fetchone()["count"])

    if dry_run:
        logger.info("AI reset dry-run matched %s courses.", target_count)
        return target_count

    set_parts = ["updated_at = CURRENT_TIMESTAMP"]
    if process_summary:
        set_parts.extend(
            [
                "ai_category = NULL",
                "ai_tags = NULL",
                "ai_summary = NULL",
                "is_ai_processed = FALSE",
            ]
        )
    if process_title:
        set_parts.extend(
            [
                "ai_title_processed = FALSE",
                "ai_title_confidence = NULL",
                "ai_title_result = NULL",
            ]
        )
        if restore_title_from_raw:
            set_parts.append("title = COALESCE(title_raw, title)")
        if reset_target_fields:
            set_parts.extend(
                [
                    "target = NULL",
                    "target_age_group = NULL",
                    "target_min_age = NULL",
                    "target_max_age = NULL",
                    "target_with_parent = FALSE",
                    "target_tags = ARRAY[]::text[]",
                    "target_age_is_explicit = FALSE",
                ]
            )

    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            WITH target_rows AS (
                SELECT id
                FROM courses
                WHERE {where_clause}
                ORDER BY updated_at NULLS FIRST, created_at NULLS FIRST, id
                {limit_clause}
            )
            UPDATE courses
            SET {", ".join(set_parts)}
            WHERE id IN (SELECT id FROM target_rows)
            """,
            params,
        )
        reset_count = cursor.rowcount

    logger.info("AI processing state reset for %s courses.", reset_count)
    return reset_count


def update_course_title_ai_data(course_id, title_result: dict) -> None:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE courses
            SET
                title = %(title)s,
                target = CASE
                    WHEN COALESCE(target_age_is_explicit, FALSE)
                     AND (target_min_age IS NOT NULL OR target_max_age IS NOT NULL)
                     AND (target_min_age IS NULL OR target_max_age IS NULL OR target_min_age <= target_max_age)
                    THEN target
                    WHEN %(clear_target_text)s THEN NULL
                    WHEN %(target)s IS NOT NULL
                    THEN %(target)s
                    ELSE COALESCE(NULLIF(btrim(target), ''), %(target)s)
                END,
                target_age_group = CASE
                    WHEN COALESCE(target_age_is_explicit, FALSE)
                     AND (target_min_age IS NOT NULL OR target_max_age IS NOT NULL)
                     AND (target_min_age IS NULL OR target_max_age IS NULL OR target_min_age <= target_max_age)
                    THEN target_age_group
                    WHEN %(target)s IS NOT NULL
                     AND (%(target_min_age)s IS NOT NULL OR %(target_max_age)s IS NOT NULL)
                    THEN %(target_age_group)s
                    WHEN target_min_age IS NOT NULL
                     AND target_max_age IS NOT NULL
                     AND target_min_age > target_max_age
                    THEN %(target_age_group)s
                    WHEN %(clear_target_text)s AND %(target_age_group)s IS NULL THEN NULL
                    WHEN %(target_age_group)s IS NOT NULL
                     AND (
                        %(target_min_age)s IS NOT NULL
                        OR %(target_max_age)s IS NOT NULL
                        OR target_age_group IS NULL
                        OR %(clear_target_age_bounds)s
                     )
                    THEN %(target_age_group)s
                    WHEN target_min_age IS NOT NULL OR target_max_age IS NOT NULL
                    THEN target_age_group
                    ELSE COALESCE(%(target_age_group)s, target_age_group)
                END,
                target_min_age = CASE
                    WHEN COALESCE(target_age_is_explicit, FALSE)
                     AND (target_min_age IS NOT NULL OR target_max_age IS NOT NULL)
                     AND (target_min_age IS NULL OR target_max_age IS NULL OR target_min_age <= target_max_age)
                    THEN target_min_age
                    WHEN %(target)s IS NOT NULL
                     AND (%(target_min_age)s IS NOT NULL OR %(target_max_age)s IS NOT NULL)
                    THEN %(target_min_age)s
                    WHEN target_min_age IS NOT NULL
                     AND target_max_age IS NOT NULL
                     AND target_min_age > target_max_age
                    THEN %(target_min_age)s
                    WHEN %(clear_target_age_bounds)s THEN NULL
                    WHEN %(clear_target_text)s AND %(target_min_age)s IS NULL THEN NULL
                    ELSE COALESCE(%(target_min_age)s, target_min_age)
                END,
                target_max_age = CASE
                    WHEN COALESCE(target_age_is_explicit, FALSE)
                     AND (target_min_age IS NOT NULL OR target_max_age IS NOT NULL)
                     AND (target_min_age IS NULL OR target_max_age IS NULL OR target_min_age <= target_max_age)
                    THEN target_max_age
                    WHEN %(target)s IS NOT NULL
                     AND (%(target_min_age)s IS NOT NULL OR %(target_max_age)s IS NOT NULL)
                    THEN %(target_max_age)s
                    WHEN target_min_age IS NOT NULL
                     AND target_max_age IS NOT NULL
                     AND target_min_age > target_max_age
                    THEN %(target_max_age)s
                    WHEN %(clear_target_age_bounds)s THEN NULL
                    WHEN %(clear_target_text)s AND %(target_max_age)s IS NULL THEN NULL
                    ELSE COALESCE(%(target_max_age)s, target_max_age)
                END,
                target_with_parent = CASE
                    WHEN %(clear_target_text)s THEN %(target_with_parent)s
                    ELSE COALESCE(target_with_parent, FALSE) OR %(target_with_parent)s
                END,
                target_tags = CASE
                    WHEN %(clear_target_text)s THEN %(target_tags)s
                    WHEN COALESCE(array_length(%(target_tags)s::text[], 1), 0) > 0
                     AND (
                        %(target)s IS NOT NULL
                        OR %(target_age_group)s IS NOT NULL
                        OR %(target_min_age)s IS NOT NULL
                        OR %(target_max_age)s IS NOT NULL
                     )
                    THEN %(target_tags)s
                    WHEN COALESCE(array_length(target_tags, 1), 0) > 0
                    THEN target_tags
                    ELSE %(target_tags)s
                END,
                title_prefix_removed = COALESCE(%(title_prefix_removed)s, title_prefix_removed),
                ai_title_processed = TRUE,
                ai_title_confidence = %(ai_title_confidence)s,
                ai_title_result = %(ai_title_result)s::jsonb,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = CAST(%(id)s AS uuid)
            """,
            {
                "id": str(course_id),
                "title": title_result["title"],
                "target": title_result.get("target"),
                "target_age_group": title_result.get("target_age_group"),
                "target_min_age": title_result.get("target_min_age"),
                "target_max_age": title_result.get("target_max_age"),
                "target_with_parent": title_result.get("target_with_parent", False),
                "target_tags": title_result.get("target_tags") or [],
                "title_prefix_removed": title_result.get("title_prefix_removed"),
                "ai_title_confidence": title_result.get("ai_title_confidence"),
                "clear_target_age_bounds": bool(title_result.get("clear_target_age_bounds")),
                "clear_target_text": bool(title_result.get("clear_target_text")),
                "ai_title_result": json.dumps(title_result.get("ai_title_result") or {}, ensure_ascii=False),
            },
        )


def mark_course_title_ai_unchanged(course_id, title_result: dict) -> None:
    stored_result = dict(title_result.get("ai_title_result") or {})
    stored_result["source"] = "title_unchanged"
    stored_result["clean_title"] = title_result.get("title")
    stored_result["unchanged_reason"] = "clean_title equals current title"

    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE courses
            SET
                ai_title_processed = FALSE,
                ai_title_confidence = %(ai_title_confidence)s,
                ai_title_result = %(ai_title_result)s::jsonb,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = CAST(%(id)s AS uuid)
            """,
            {
                "id": str(course_id),
                "ai_title_confidence": title_result.get("ai_title_confidence"),
                "ai_title_result": json.dumps(stored_result, ensure_ascii=False),
            },
        )


def update_course_ai_data(course_id, ai_result: dict) -> None:
    with get_db_cursor() as cursor:
        tags_json = json.dumps(ai_result["tags"], ensure_ascii=False)
        cursor.execute(
            """
            UPDATE courses
            SET
                ai_category = %(category)s,
                ai_tags = %(tags)s,
                ai_summary = %(summary)s,
                is_ai_processed = TRUE,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = CAST(%(id)s AS uuid)
            """,
            {
                "category": ai_result["category"],
                "tags": tags_json,
                "summary": ai_result["summary"],
                "id": str(course_id),
            },
        )


def should_skip_retry(course_id: str, retry_state: Dict[str, float]) -> bool:
    next_retry_at = retry_state.get(course_id)
    return bool(next_retry_at and next_retry_at > time.time())


def mark_retry(course_id: str, retry_state: Dict[str, float], retry_wait: float) -> None:
    retry_state[course_id] = time.time() + retry_wait


def clear_retry(course_id: str, retry_state: Dict[str, float]) -> None:
    retry_state.pop(course_id, None)


def retry_state_lock(retry_lock: Optional[threading.Lock]):
    return retry_lock if retry_lock else _null_retry_lock()


@contextmanager
def _null_retry_lock() -> Iterator[None]:
    yield


def process_course(
    processor: AIProcessor,
    course: dict,
    retry_state: Dict[str, float],
    retry_wait: float,
    process_title: bool = True,
    process_summary: bool = True,
    retry_lock: Optional[threading.Lock] = None,
) -> bool:
    course_id = str(course["id"])

    with retry_state_lock(retry_lock):
        if should_skip_retry(course_id, retry_state):
            logger.info("Skipping %s until retry window opens.", course_id)
            return False

    with course_ai_lock(course_id) as locked:
        if not locked:
            logger.info("Skipping %s because another AI worker holds the advisory lock.", course_id)
            return False

        locked_course = fetch_course_for_ai(course_id)
        if not locked_course:
            logger.info("Skipping %s because the course is inactive or no longer exists.", course_id)
            return False
        if not course_needs_ai_work(
            locked_course,
            process_title=process_title,
            process_summary=process_summary,
        ):
            with retry_state_lock(retry_lock):
                clear_retry(course_id, retry_state)
            logger.info("Skipping %s because AI fields are already current.", course_id)
            return False
        return _process_locked_course(
            processor,
            locked_course,
            retry_state,
            retry_wait,
            process_title=process_title,
            process_summary=process_summary,
            retry_lock=retry_lock,
        )


def _process_locked_course(
    processor: AIProcessor,
    course: dict,
    retry_state: Dict[str, float],
    retry_wait: float,
    process_title: bool = True,
    process_summary: bool = True,
    retry_lock: Optional[threading.Lock] = None,
) -> bool:
    course_id = str(course["id"])

    logger.info("Processing [%s] %s", course_id, course["title"])
    description = course.get("description") or ""
    did_work = False

    if process_title and not course.get("ai_title_processed"):
        title_result = processor.analyze_title(course)
        record_ai_node_metric(processor, course["id"], "title", bool(title_result))
        if title_result:
            if title_ai_changed(course, title_result):
                update_course_title_ai_data(course["id"], title_result)
                course["title"] = title_result["title"]
                course["target"] = title_result.get("target")
                course["ai_title_processed"] = True
                did_work = True
                logger.info(
                    "Title split [%s] -> title=%s target=%s age=%s %s~%s confidence=%s",
                    course_id,
                    title_result["title"],
                    title_result.get("target"),
                    title_result.get("target_age_group"),
                    title_result.get("target_min_age"),
                    title_result.get("target_max_age"),
                    title_result.get("ai_title_confidence"),
                )
            else:
                mark_course_title_ai_unchanged(course["id"], title_result)
                course["ai_title_processed"] = False
                logger.info(
                    "Title AI unchanged [%s] -> title=%s. Marked as title_unchanged and skipped.",
                    course_id,
                    title_result.get("title"),
                )

    if not process_summary:
        with retry_state_lock(retry_lock):
            clear_retry(course_id, retry_state)
        return did_work

    if course.get("is_ai_processed"):
        with retry_state_lock(retry_lock):
            clear_retry(course_id, retry_state)
        return did_work

    result = processor.analyze_course(course["title"], description, course.get("category_raw") or "")
    record_ai_node_metric(processor, course["id"], "summary", bool(result))

    if not result:
        with retry_state_lock(retry_lock):
            mark_retry(course_id, retry_state, retry_wait)
        logger.warning("AI analysis failed for %s. Will retry later.", course_id)
        return False

    update_course_ai_data(course["id"], result)
    with retry_state_lock(retry_lock):
        clear_retry(course_id, retry_state)
    logger.info("Completed [%s] -> %s", course_id, result["category"])
    return True


def process_course_batch(
    courses: Sequence[dict],
    retry_state: Dict[str, float],
    retry_wait: float,
    process_title: bool = True,
    process_summary: bool = True,
    workers: int = 1,
    delay: float = 0.0,
) -> int:
    workers = max(1, workers)
    if workers <= 1:
        processor = AIProcessor()
        if process_summary and not processor.provider:
            logger.error("AI tag provider is not ready. Aborting batch.")
            return 0

        success_count = 0
        for course in courses:
            if not RUNNING:
                break
            if process_course(
                processor,
                course,
                retry_state,
                retry_wait,
                process_title=process_title,
                process_summary=process_summary,
            ):
                success_count += 1
            time.sleep(delay)
        return success_count

    thread_local = threading.local()
    retry_lock = threading.Lock()
    hosts = ollama_worker_hosts()
    host_index = 0
    host_lock = threading.Lock()

    def processor_for_thread() -> Optional[AIProcessor]:
        nonlocal host_index
        processor = getattr(thread_local, "processor", None)
        if processor is None:
            with host_lock:
                host = hosts[host_index % len(hosts)]
                host_index += 1
            processor = AIProcessor(ollama_url=host)
            thread_local.processor = processor
        return processor if processor.provider or not process_summary else None

    def task(course: dict) -> bool:
        if not RUNNING:
            return False
        processor = processor_for_thread()
        if not processor:
            logger.error("AI tag provider is not ready in worker thread.")
            return False
        return process_course(
            processor,
            course,
            retry_state,
            retry_wait,
            process_title=process_title,
            process_summary=process_summary,
            retry_lock=retry_lock,
        )

    success_count = 0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ai-worker") as executor:
        futures = []
        for course in courses:
            if not RUNNING:
                break
            futures.append(executor.submit(task, course))
            if delay > 0:
                time.sleep(delay)

        for future in as_completed(futures):
            try:
                if future.result():
                    success_count += 1
            except Exception as exc:
                logger.exception("AI worker thread failed: %s", exc)
    return success_count


def run_once(
    limit: int,
    delay: float,
    retry_wait: float,
    process_title: bool = True,
    process_summary: bool = True,
    providers: Optional[Sequence[str]] = None,
    workers: int = 1,
) -> int:
    retry_state: Dict[str, float] = {}
    courses = fetch_unprocessed_courses(
        limit,
        process_title=process_title,
        process_summary=process_summary,
        providers=providers,
    )
    logger.info("Found %s unprocessed courses. workers=%s", len(courses), workers)

    success_count = process_course_batch(
        courses,
        retry_state,
        retry_wait,
        process_title=process_title,
        process_summary=process_summary,
        workers=workers,
        delay=delay,
    )
    logger.info("One-shot run completed. Processed %s/%s courses.", success_count, len(courses))
    return success_count


def run_worker(
    batch_size: int,
    delay: float,
    poll_interval: float,
    retry_wait: float,
    active_start: datetime_time,
    active_end: datetime_time,
    active_check_interval: float,
    enforce_active_window: bool,
    weekend_24h: bool = False,
    process_title: bool = True,
    process_summary: bool = True,
    providers: Optional[Sequence[str]] = None,
    max_cycles: Optional[int] = None,
    workers: int = 1,
) -> None:
    if not acquire_worker_lock():
        return

    processor = AIProcessor()
    if process_summary and not processor.provider:
        logger.error("AI tag provider is not ready. Worker will not start.")
        release_worker_lock()
        return

    retry_state: Dict[str, float] = {}
    cycle = 0
    schedule_label = active_schedule_label(active_start, active_end, weekend_24h)
    logger.info(
        "AI worker started. batch_size=%s, workers=%s, delay=%ss, poll_interval=%ss, active_schedule=%s, enforce=%s",
        batch_size,
        workers,
        delay,
        poll_interval,
        schedule_label,
        enforce_active_window,
    )

    try:
        while RUNNING:
            cycle += 1
            now = datetime.now()
            if enforce_active_window and not is_within_ai_active_schedule(now, active_start, active_end, weekend_24h):
                wait_seconds = min(
                    active_check_interval,
                    seconds_until_ai_active_schedule(now, active_start, active_end, weekend_24h),
                )
                logger.info(
                    "Outside AI active schedule (%s). Sleeping for %ss.",
                    schedule_label,
                    wait_seconds,
                )
                if max_cycles and cycle >= max_cycles:
                    logger.info("Reached max cycles. Stopping worker.")
                    break
                time.sleep(wait_seconds)
                continue

            courses = fetch_unprocessed_courses(
                batch_size,
                process_title=process_title,
                process_summary=process_summary,
                providers=providers,
            )

            if not courses:
                logger.info("No unprocessed courses found. Sleeping for %ss.", poll_interval)
                if max_cycles and cycle >= max_cycles:
                    logger.info("Reached max cycles. Stopping worker.")
                    break
                time.sleep(poll_interval)
                continue

            if workers <= 1:
                processed_count = 0
                for course in courses:
                    if not RUNNING:
                        break
                    if enforce_active_window and not is_within_ai_active_schedule(datetime.now(), active_start, active_end, weekend_24h):
                        logger.info("AI active schedule ended. Pausing remaining courses until next window.")
                        break
                    if process_course(
                        processor,
                        course,
                        retry_state,
                        retry_wait,
                        process_title=process_title,
                        process_summary=process_summary,
                    ):
                        processed_count += 1
                    time.sleep(delay)
            else:
                processed_count = process_course_batch(
                    courses,
                    retry_state,
                    retry_wait,
                    process_title=process_title,
                    process_summary=process_summary,
                    workers=workers,
                    delay=delay,
                )

            logger.info("Cycle %s finished. Processed %s/%s fetched courses.", cycle, processed_count, len(courses))

            if max_cycles and cycle >= max_cycles:
                logger.info("Reached max cycles. Stopping worker.")
                break

            time.sleep(poll_interval)
    finally:
        release_worker_lock()
        logger.info("AI worker stopped.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI analysis worker for unprocessed courses")
    parser.add_argument("--limit", type=int, default=10, help="Number of courses to process in one-shot mode")
    parser.add_argument("--batch-size", type=int, default=10, help="Number of courses to fetch per worker cycle")
    parser.add_argument(
        "--workers",
        type=int,
        default=env_int("AI_WORKERS", 1, 1, 16),
        help="Number of courses to process concurrently. Defaults to AI_WORKERS or 1.",
    )
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between AI requests in seconds")
    parser.add_argument("--poll-interval", type=float, default=60.0, help="Sleep time when worker waits for new data")
    parser.add_argument("--retry-wait", type=float, default=300.0, help="Retry delay for failed items in seconds")
    parser.add_argument("--active-start", type=parse_clock, default=parse_clock("22:00"), help="Worker active window start, HH:MM")
    parser.add_argument("--active-end", type=parse_clock, default=parse_clock("07:00"), help="Worker active window end, HH:MM")
    parser.add_argument(
        "--active-check-interval",
        type=float,
        default=1800.0,
        help="Sleep interval while outside the active window, in seconds",
    )
    parser.add_argument(
        "--ignore-active-window",
        action="store_true",
        help="Run worker immediately regardless of the configured active window",
    )
    parser.add_argument(
        "--weekend-24h",
        action="store_true",
        default=env_flag("AI_WEEKEND_24H", False),
        help="Process AI jobs all day on Saturday and Sunday while keeping the weekday active window",
    )
    parser.add_argument("--once", action="store_true", help="Run one batch and exit")
    parser.add_argument("--max-cycles", type=int, default=None, help="Optional cycle limit for worker mode")
    parser.add_argument("--title-only", action="store_true", help="Only split/clean title and age target fields")
    parser.add_argument("--summary-only", action="store_true", help="Only generate AI summary/category/tags")
    parser.add_argument("--provider", action="append", default=None, help="Limit processing to a provider. Can be repeated.")
    parser.add_argument("--reset-ai", action="store_true", help="Reset AI flags/output before processing.")
    parser.add_argument(
        "--from-scratch",
        action="store_true",
        help="Reset AI flags/output first, then process from the beginning.",
    )
    parser.add_argument("--reset-only", action="store_true", help="Reset AI flags/output and exit without processing.")
    parser.add_argument("--reset-dry-run", action="store_true", help="Show how many rows would be reset, then exit.")
    parser.add_argument("--reset-limit", type=int, default=None, help="Limit rows reset by --reset-ai.")
    parser.add_argument(
        "--keep-current-title",
        action="store_true",
        help="Do not restore title from title_raw when resetting title AI.",
    )
    parser.add_argument(
        "--reset-target-fields",
        action="store_true",
        help="Clear target/age fields so title AI recomputes them from title_raw/category.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_shutdown)

    args = parse_args()
    if args.from_scratch:
        args.reset_ai = True

    process_title = not args.summary_only
    process_summary = not args.title_only
    providers = [provider.upper() for provider in args.provider] if args.provider else None

    if args.reset_ai or args.reset_dry_run:
        reset_ai_processing(
            process_title=process_title,
            process_summary=process_summary,
            providers=providers,
            limit=args.reset_limit,
            restore_title_from_raw=not args.keep_current_title,
            reset_target_fields=args.reset_target_fields,
            dry_run=args.reset_dry_run,
        )
        if args.reset_dry_run or args.reset_only:
            sys.exit(0)

    if args.once:
        run_once(
            limit=args.limit,
            delay=args.delay,
            retry_wait=args.retry_wait,
            process_title=process_title,
            process_summary=process_summary,
            providers=providers,
            workers=args.workers,
        )
    else:
        run_worker(
            batch_size=args.batch_size,
            delay=args.delay,
            poll_interval=args.poll_interval,
            retry_wait=args.retry_wait,
            active_start=args.active_start,
            active_end=args.active_end,
            active_check_interval=args.active_check_interval,
            enforce_active_window=not args.ignore_active_window,
            weekend_24h=args.weekend_24h,
            process_title=process_title,
            process_summary=process_summary,
            providers=providers,
            max_cycles=args.max_cycles,
            workers=args.workers,
        )
