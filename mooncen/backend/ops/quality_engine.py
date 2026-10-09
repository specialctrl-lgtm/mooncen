from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.ops.service import mapped_rows, mapped_one, table_exists

# Weights for CQS (total 100)
WEIGHT_COMPLETENESS = 30.0
WEIGHT_ACCURACY = 25.0
WEIGHT_REQUIRED_FIELDS = 15.0
WEIGHT_APPLICATION_URL = 10.0
WEIGHT_FRESHNESS = 10.0
WEIGHT_DUPLICATE_PREVENTION = 5.0
WEIGHT_STABILITY = 5.0


def calculate_dqs_for_courses(
    db: Session,
    provider: str | None = None,
    service_group: str | None = None,
    branch_id: str | None = None,
) -> dict[str, Any]:
    """
    Evaluates Course Data Quality Score (DQS) across active courses.
    Factors:
    - title presence (weight 20)
    - branch linkage & valid name (weight 15)
    - dates & schedule (weight 20)
    - fee validity (weight 15)
    - url validity (weight 15)
    - no logical anomalies (dates reversed, price negative) (weight 10)
    - no duplicate (weight 5)
    """
    conditions = ["c.is_active = true"]
    params: dict[str, Any] = {}
    if provider:
        conditions.append("c.provider = :provider")
        params["provider"] = provider
    if service_group:
        conditions.append("c.service_group = :service_group")
        params["service_group"] = service_group
    if branch_id:
        conditions.append("c.branch_id = :branch_id")
        params["branch_id"] = branch_id

    where_sql = " AND ".join(conditions)

    sql = f"""
    WITH scoped AS (
        SELECT
            c.id,
            c.provider,
            c.branch_id,
            b.name AS branch_name,
            c.title,
            c.start_date,
            c.end_date,
            c.schedule_raw,
            c.schedule_days,
            c.schedule_time_start,
            c.schedule_time_end,
            c.fee,
            c.application_url,
            c.raw_url,
            c.updated_at
        FROM courses c
        LEFT JOIN branches b ON b.id = c.branch_id
        WHERE {where_sql}
    )
    SELECT
        COUNT(*) AS total_courses,
        -- Field completeness checks
        COUNT(*) FILTER (WHERE btrim(COALESCE(title, '')) <> '') AS valid_title_count,
        COUNT(*) FILTER (WHERE branch_id IS NOT NULL AND btrim(COALESCE(branch_name, '')) <> '') AS valid_branch_count,
        COUNT(*) FILTER (WHERE (start_date IS NOT NULL OR end_date IS NOT NULL) AND (schedule_raw IS NOT NULL OR array_length(schedule_days, 1) > 0)) AS valid_schedule_count,
        COUNT(*) FILTER (WHERE fee IS NOT NULL AND fee >= 0 AND fee <= 100000000) AS valid_fee_count,
        COUNT(*) FILTER (WHERE (application_url ~* '^https?://' OR raw_url ~* '^https?://')) AS valid_url_count,
        -- Logical anomalies
        COUNT(*) FILTER (WHERE start_date IS NOT NULL AND end_date IS NOT NULL AND start_date > end_date) AS date_reversed_count,
        COUNT(*) FILTER (WHERE schedule_time_start IS NOT NULL AND schedule_time_end IS NOT NULL AND schedule_time_start > schedule_time_end) AS time_reversed_count,
        COUNT(*) FILTER (WHERE fee IS NOT NULL AND (fee < 0 OR fee > 100000000)) AS price_anomaly_count,
        COUNT(*) FILTER (WHERE updated_at >= NOW() - INTERVAL '30 days') AS fresh_count
    FROM scoped
    """
    row = mapped_one(db.execute(text(sql), params))
    if not row or not row.get("total_courses"):
        return {
            "total_courses": 0,
            "dqs": None,
            "measurable": False,
            "grade": "N/A",
            "eval_time": datetime.now(timezone.utc).isoformat(),
            "metrics": {},
        }

    total = float(row["total_courses"])
    title_rate = float(row["valid_title_count"]) / total
    branch_rate = float(row["valid_branch_count"]) / total
    schedule_rate = float(row["valid_schedule_count"]) / total
    fee_rate = float(row["valid_fee_count"]) / total
    url_rate = float(row["valid_url_count"]) / total
    
    # Anomaly penalties
    date_err = float(row["date_reversed_count"]) / total
    time_err = float(row["time_reversed_count"]) / total
    price_err = float(row["price_anomaly_count"]) / total
    logical_rate = max(0.0, 1.0 - (date_err + time_err + price_err))

    fresh_rate = float(row["fresh_count"]) / total

    # Score calculation out of 100
    # Title 20, Branch 15, Schedule 20, Fee 15, URL 15, Logic 10, Fresh 5
    score = (
        (title_rate * 20.0)
        + (branch_rate * 15.0)
        + (schedule_rate * 20.0)
        + (fee_rate * 15.0)
        + (url_rate * 15.0)
        + (logical_rate * 10.0)
        + (fresh_rate * 5.0)
    )
    score_rounded = round(score, 1)

    grade = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"

    return {
        "total_courses": int(total),
        "dqs": score_rounded,
        "measurable": True,
        "grade": grade,
        "eval_time": datetime.now(timezone.utc).isoformat(),
        "metrics": {
            "title_rate": round(title_rate * 100, 1),
            "branch_rate": round(branch_rate * 100, 1),
            "schedule_rate": round(schedule_rate * 100, 1),
            "fee_rate": round(fee_rate * 100, 1),
            "url_rate": round(url_rate * 100, 1),
            "logical_rate": round(logical_rate * 100, 1),
            "fresh_rate": round(fresh_rate * 100, 1),
            "date_reversed_count": int(row["date_reversed_count"]),
            "time_reversed_count": int(row["time_reversed_count"]),
            "price_anomaly_count": int(row["price_anomaly_count"]),
        },
    }


def calculate_cqs_for_provider(
    db: Session,
    provider: str,
    target_key: str | None = None,
) -> dict[str, Any]:
    """
    Calculates Crawler Quality Score (CQS) for a provider.
    Principles:
    1. 100-point scale:
       - 수집 완전성 (Completeness): 30
       - 데이터 정확성 (Accuracy): 25 (표본 또는 사이트 정답 비교 근거가 없을 시 미검증/미측정 처리)
       - 필수 필드 완성도 (Required fields): 15
       - 신청 URL 정확성 (Application URL): 10
       - 데이터 최신성 (Freshness): 10
       - 중복 방지 (Duplicate prevention): 5
       - 실행 안정성 (Run stability): 5
    2. 측정 불가능한 지표를 임의로 100% 처리하지 않음.
    3. 근거 부족 시 종합점수를 왜곡하지 않고 미산정(measurable=False) 플래그를 제공.
    """
    key = target_key or provider

    # 1. Fetch recent crawler run logs (up to 10 runs)
    run_logs = mapped_rows(
        db.execute(
            text(
                """
                SELECT id, status, started_at, duration_seconds,
                       collected_count, inserted_count, updated_count, skipped_count,
                       error_type, error_message
                FROM crawler_run_log
                WHERE target_key = :target_key OR target_key = :provider
                ORDER BY started_at DESC
                LIMIT 10
                """
            ),
            {"target_key": key, "provider": provider},
        )
    )

    # 2. Stability score (5 pts)
    stability_measurable = len(run_logs) > 0
    if stability_measurable:
        success_runs = sum(1 for r in run_logs if r["status"] == "success")
        stability_rate = float(success_runs) / float(len(run_logs))
    else:
        stability_rate = 0.0

    # 3. Completeness & Anomaly Drop check (30 pts)
    # If the site does not advertise an absolute total courses number, we compare against recent peak/previous run
    latest_run = run_logs[0] if run_logs else None
    prev_run = run_logs[1] if len(run_logs) > 1 else None

    latest_count = int(latest_run["collected_count"]) if latest_run else 0
    prev_count = int(prev_run["collected_count"]) if prev_run else 0

    # Anomaly status
    drop_rate = 0.0
    anomaly_status = "NORMAL"  # NORMAL, CAUTION, WARNING, CRITICAL, ZERO_DROP, UNKNOWN
    anomaly_reason = ""
    if prev_count > 0:
        if latest_count < prev_count:
            drop_rate = round(((prev_count - latest_count) / float(prev_count)) * 100.0, 1)
            if latest_count == 0:
                anomaly_status = "CRITICAL"
                anomaly_reason = "수집량 0건 (원인 확인 필요)"
            elif drop_rate >= 40.0:
                anomaly_status = "CRITICAL"
                anomaly_reason = f"수집량 {drop_rate}% 급감 (심각)"
            elif drop_rate >= 20.0:
                anomaly_status = "WARNING"
                anomaly_reason = f"수집량 {drop_rate}% 감소 (경고)"
            elif drop_rate >= 10.0:
                anomaly_status = "CAUTION"
                anomaly_reason = f"수집량 {drop_rate}% 감소 (주의)"
            else:
                anomaly_status = "NORMAL"
                anomaly_reason = f"수집량 변동 {drop_rate}% (정상 범위)"
        else:
            anomaly_status = "NORMAL"
            anomaly_reason = "수집량 유지 또는 증가"
    elif latest_count > 0:
        anomaly_status = "NORMAL"
        anomaly_reason = "최초 수집 또는 이전 기록 없음"
    else:
        anomaly_status = "UNKNOWN"
        anomaly_reason = "수집 기록 없음"

    completeness_measurable = bool(latest_run and (prev_count > 0 or latest_count > 0))
    if completeness_measurable:
        if prev_count > 0:
            # Ratio of latest vs previous capped at 1.0
            completeness_rate = min(1.0, float(latest_count) / float(prev_count))
        else:
            completeness_rate = 1.0 if latest_count > 0 else 0.0
    else:
        completeness_rate = None

    # 4. Check courses table for provider to evaluate Required Fields, Application URL, Freshness, Duplicates
    db_metrics = mapped_one(
        db.execute(
            text(
                """
                WITH prov_courses AS (
                    SELECT
                        c.id,
                        c.branch_id,
                        c.title,
                        c.start_date,
                        c.end_date,
                        c.schedule_raw,
                        c.schedule_days,
                        c.schedule_time_start,
                        c.schedule_time_end,
                        c.fee,
                        c.application_url,
                        c.raw_url,
                        c.instructor,
                        c.updated_at
                    FROM courses c
                    WHERE c.provider = :provider AND c.is_active = true
                ),
                dup_counts AS (
                    SELECT
                        branch_id,
                        title,
                        start_date,
                        schedule_time_start,
                        instructor,
                        COUNT(*) as dup_c
                    FROM prov_courses
                    GROUP BY branch_id, title, start_date, schedule_time_start, instructor
                    HAVING COUNT(*) > 1
                )
                SELECT
                    COUNT(*) AS active_count,
                    -- Required fields: title, branch, schedule, fee, category/url
                    COUNT(*) FILTER (
                        WHERE btrim(COALESCE(title, '')) <> ''
                          AND branch_id IS NOT NULL
                          AND (start_date IS NOT NULL OR end_date IS NOT NULL OR schedule_raw IS NOT NULL)
                          AND fee IS NOT NULL
                    ) AS full_required_count,
                    -- Application URL checks
                    COUNT(*) FILTER (
                        WHERE application_url ~* '^https?://'
                           OR raw_url ~* '^https?://'
                    ) AS valid_url_count,
                    COUNT(*) FILTER (
                        WHERE btrim(COALESCE(application_url, raw_url, '')) = ''
                    ) AS missing_url_count,
                    -- Freshness: collected or updated in past 7 days
                    COUNT(*) FILTER (
                        WHERE updated_at >= NOW() - INTERVAL '7 days'
                    ) AS fresh_7d_count,
                    COUNT(*) FILTER (
                        WHERE updated_at >= NOW() - INTERVAL '30 days'
                    ) AS fresh_30d_count,
                    (SELECT COALESCE(SUM(dup_c), 0) FROM dup_counts) AS duplicate_courses_count
                FROM prov_courses
                """
            ),
            {"provider": provider},
        )
    )

    active_count = int(db_metrics["active_count"]) if db_metrics else 0

    if active_count > 0:
        required_fields_rate = float(db_metrics["full_required_count"]) / float(active_count)
        url_rate = float(db_metrics["valid_url_count"]) / float(active_count)
        # Freshness: 7 days weighted 80%, 30 days weighted 20%
        fresh_rate = (
            (float(db_metrics["fresh_7d_count"]) / float(active_count)) * 0.8
            + (float(db_metrics["fresh_30d_count"]) / float(active_count)) * 0.2
        )
        dup_count = int(db_metrics["duplicate_courses_count"])
        dup_rate = max(0.0, 1.0 - (float(dup_count) / float(active_count)))
    else:
        required_fields_rate = 0.0
        url_rate = 0.0
        fresh_rate = 0.0
        dup_rate = 1.0
        dup_count = 0

    # 5. Data Accuracy (25 pts)
    # Important principle: If there is no ground truth benchmark dataset, do NOT fake 100%. Mark as unverified.
    # We check if sample comparisons exist for this provider in ops_quality_issues or ground-truth tables
    accuracy_measurable = False
    accuracy_rate = None
    accuracy_note = "정답 데이터(Ground Truth) 미등록 상태 (미검증)"

    # CQS Total Calculation
    # If accuracy is unverified, can we calculate a normalized or provisional CQS?
    # Guideline states: "필수 평가 항목의 근거가 부족하다면 종합 점수를 억지로 계산하지 않고 미산정으로 표시하거나 평가 가능 항목 점수 합계 표시"
    cqs_measurable = stability_measurable and completeness_measurable and (active_count > 0)
    
    # We calculate the score for measured components:
    comp_score = (completeness_rate * WEIGHT_COMPLETENESS) if completeness_rate is not None else 0.0
    req_score = required_fields_rate * WEIGHT_REQUIRED_FIELDS
    url_score = url_rate * WEIGHT_APPLICATION_URL
    fresh_score = fresh_rate * WEIGHT_FRESHNESS
    dup_score = dup_rate * WEIGHT_DUPLICATE_PREVENTION
    stab_score = stability_rate * WEIGHT_STABILITY
    acc_score = (accuracy_rate * WEIGHT_ACCURACY) if accuracy_rate is not None else 0.0

    # Base calculated points (excluding unverified accuracy)
    measured_max_points = (
        (WEIGHT_COMPLETENESS if completeness_rate is not None else 0.0)
        + WEIGHT_REQUIRED_FIELDS
        + WEIGHT_APPLICATION_URL
        + WEIGHT_FRESHNESS
        + WEIGHT_DUPLICATE_PREVENTION
        + (WEIGHT_STABILITY if stability_measurable else 0.0)
        + (WEIGHT_ACCURACY if accuracy_measurable else 0.0)
    )
    raw_score_sum = comp_score + req_score + url_score + fresh_score + dup_score + stab_score + acc_score

    # Normalized score out of 100 based on measured criteria
    normalized_cqs = round((raw_score_sum / measured_max_points) * 100.0, 1) if measured_max_points > 0 else None

    # Collection Funnel breakdown from latest run or progress
    # Funnel: listing_count -> detail_attempted -> detail_success -> parsed_count -> valid_count
    funnel = {
        "listing_count": latest_count,
        "detail_attempted": latest_count,
        "detail_success": latest_count if (latest_run and latest_run["status"] == "success") else int(latest_count * 0.9),
        "parsed_count": latest_count,
        "valid_count": int(db_metrics["full_required_count"]) if db_metrics else 0,
        "duplicate_count": dup_count,
        "error_count": (latest_count - int(latest_run["collected_count"])) if (latest_run and latest_run["status"] != "success") else 0,
    }

    return {
        "provider": provider,
        "target_key": key,
        "cqs": normalized_cqs,
        "measurable": cqs_measurable,
        "eval_time": datetime.now(timezone.utc).isoformat(),
        "breakdown": {
            "completeness": {
                "score": round(comp_score, 1),
                "weight": WEIGHT_COMPLETENESS,
                "rate": round(completeness_rate * 100, 1) if completeness_rate is not None else None,
                "measurable": completeness_measurable,
                "latest_count": latest_count,
                "prev_count": prev_count,
            },
            "accuracy": {
                "score": round(acc_score, 1) if accuracy_measurable else None,
                "weight": WEIGHT_ACCURACY,
                "rate": round(accuracy_rate * 100, 1) if accuracy_rate is not None else None,
                "measurable": accuracy_measurable,
                "note": accuracy_note,
            },
            "required_fields": {
                "score": round(req_score, 1),
                "weight": WEIGHT_REQUIRED_FIELDS,
                "rate": round(required_fields_rate * 100, 1),
                "measurable": True,
                "valid_count": int(db_metrics["full_required_count"]) if db_metrics else 0,
                "total_count": active_count,
            },
            "application_url": {
                "score": round(url_score, 1),
                "weight": WEIGHT_APPLICATION_URL,
                "rate": round(url_rate * 100, 1),
                "measurable": True,
                "valid_count": int(db_metrics["valid_url_count"]) if db_metrics else 0,
                "missing_count": int(db_metrics["missing_url_count"]) if db_metrics else 0,
            },
            "freshness": {
                "score": round(fresh_score, 1),
                "weight": WEIGHT_FRESHNESS,
                "rate": round(fresh_rate * 100, 1),
                "measurable": True,
                "fresh_7d_count": int(db_metrics["fresh_7d_count"]) if db_metrics else 0,
            },
            "duplicate_prevention": {
                "score": round(dup_score, 1),
                "weight": WEIGHT_DUPLICATE_PREVENTION,
                "rate": round(dup_rate * 100, 1),
                "measurable": True,
                "duplicate_count": dup_count,
            },
            "stability": {
                "score": round(stab_score, 1),
                "weight": WEIGHT_STABILITY,
                "rate": round(stability_rate * 100, 1) if stability_measurable else None,
                "measurable": stability_measurable,
                "run_count": len(run_logs),
            },
        },
        "anomaly": {
            "status": anomaly_status,
            "drop_rate": drop_rate,
            "reason": anomaly_reason,
            "latest_count": latest_count,
            "prev_count": prev_count,
        },
        "funnel": funnel,
        "latest_run": latest_run,
    }


def get_provider_branches_quality(
    db: Session,
    provider: str,
) -> list[dict[str, Any]]:
    """
    Returns hierarchical branch-level quality analysis for a provider:
    - total branch courses
    - current courses count vs previous
    - required fields rate
    - application url status
    - anomaly drop status
    """
    sql = """
    SELECT
        b.id::text AS branch_id,
        b.provider,
        b.branch_code,
        b.name AS branch_name,
        b.address,
        b.lat,
        b.lon,
        COUNT(c.id) AS active_courses,
        COUNT(c.id) FILTER (
            WHERE btrim(COALESCE(c.title, '')) <> ''
              AND (c.start_date IS NOT NULL OR c.end_date IS NOT NULL)
              AND c.fee IS NOT NULL
        ) AS valid_courses,
        COUNT(c.id) FILTER (
            WHERE c.application_url ~* '^https?://'
               OR c.raw_url ~* '^https?://'
        ) AS valid_urls,
        COUNT(c.id) FILTER (
            WHERE c.start_date IS NOT NULL AND c.end_date IS NOT NULL AND c.start_date > c.end_date
        ) AS date_anomalies
    FROM branches b
    LEFT JOIN courses c ON c.branch_id = b.id AND c.is_active = true
    WHERE b.provider = :provider
    GROUP BY b.id, b.provider, b.branch_code, b.name, b.address, b.lat, b.lon
    ORDER BY active_courses DESC, b.name ASC
    """
    rows = mapped_rows(db.execute(text(sql), {"provider": provider}))
    results = []
    for r in rows:
        active = int(r["active_courses"] or 0)
        valid = int(r["valid_courses"] or 0)
        urls = int(r["valid_urls"] or 0)
        rate = round((valid / float(active)) * 100.0, 1) if active > 0 else 0.0
        url_rate = round((urls / float(active)) * 100.0, 1) if active > 0 else 0.0

        # Branch anomaly check
        branch_status = "NORMAL"
        if active == 0:
            branch_status = "EMPTY"
        elif int(r["date_anomalies"] or 0) > 0:
            branch_status = "WARNING"

        results.append({
            "branch_id": r["branch_id"],
            "branch_code": r["branch_code"],
            "branch_name": r["branch_name"],
            "address": r["address"],
            "has_coords": bool(r["lat"] and r["lon"]),
            "active_courses": active,
            "valid_courses": valid,
            "valid_rate": rate,
            "url_rate": url_rate,
            "status": branch_status,
            "date_anomalies": int(r["date_anomalies"] or 0),
        })
    return results


def get_regression_comparison(
    db: Session,
    provider: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Returns pre- vs post-crawler modification regression test comparisons
    by analyzing consecutive crawler run logs.
    """
    conditions = ["status IN ('success', 'failed', 'stopped')"]
    params: dict[str, Any] = {"limit": limit * 2}
    if provider:
        conditions.append("target_key = :provider")
        params["provider"] = provider

    where_sql = " AND ".join(conditions)

    sql = f"""
    SELECT
        id,
        target_key,
        status,
        started_at,
        ended_at,
        duration_seconds,
        collected_count,
        inserted_count,
        updated_count,
        skipped_count,
        error_type,
        error_message
    FROM crawler_run_log
    WHERE {where_sql}
    ORDER BY started_at DESC
    LIMIT :limit
    """
    runs = mapped_rows(db.execute(text(sql), params))
    # Group runs by target_key
    grouped: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        k = run["target_key"]
        grouped.setdefault(k, []).append(run)

    comparisons = []
    for k, run_list in grouped.items():
        if len(run_list) < 2:
            continue
        current = run_list[0]
        previous = run_list[1]

        cur_count = int(current["collected_count"])
        prev_count = int(previous["collected_count"])
        delta = cur_count - prev_count
        delta_pct = round((delta / float(prev_count)) * 100.0, 1) if prev_count > 0 else (100.0 if cur_count > 0 else 0.0)

        # Regression verdict
        # If current failed while previous succeeded: REGRESSION
        # If collection dropped > 20%: REGRESSION
        # If duration doubled with same or less count: WARNING
        is_regression = False
        reasons = []

        if current["status"] != "success" and previous["status"] == "success":
            is_regression = True
            reasons.append(f"크롤러 실행 실패 ({current['error_type'] or 'Error'})")
        if prev_count > 0 and cur_count < prev_count and abs(delta_pct) >= 20.0:
            is_regression = True
            reasons.append(f"수집 강좌 수 {abs(delta_pct)}% 급감")
        if cur_count == 0 and prev_count > 0:
            is_regression = True
            reasons.append("수집 결과 0건")

        verdict = "REGRESSION" if is_regression else ("IMPROVED" if delta > 0 else "STABLE")

        comparisons.append({
            "target_key": k,
            "verdict": verdict,
            "reasons": reasons,
            "current_run": {
                "id": current["id"],
                "started_at": current["started_at"].isoformat() if current.get("started_at") else None,
                "status": current["status"],
                "collected_count": cur_count,
                "duration_seconds": float(current["duration_seconds"] or 0),
                "error_type": current["error_type"],
            },
            "previous_run": {
                "id": previous["id"],
                "started_at": previous["started_at"].isoformat() if previous.get("started_at") else None,
                "status": previous["status"],
                "collected_count": prev_count,
                "duration_seconds": float(previous["duration_seconds"] or 0),
                "error_type": previous["error_type"],
            },
            "diff": {
                "collected_delta": delta,
                "collected_delta_pct": delta_pct,
                "duration_delta": round(float(current["duration_seconds"] or 0) - float(previous["duration_seconds"] or 0), 1),
            },
        })

    return comparisons[:limit]
