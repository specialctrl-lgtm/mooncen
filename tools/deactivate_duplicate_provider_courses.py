from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.db_utils import get_db_connection


TARGET_DIR = ROOT / "config" / "crawl_targets"


def _targets_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("targets")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def duplicate_config_evidence(duplicate_provider: str, canonical_provider: str) -> list[str]:
    evidence: list[str] = []
    for path in sorted(TARGET_DIR.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        for index, target in enumerate(_targets_from_payload(payload)):
            if str(target.get("provider") or "").strip() != duplicate_provider:
                continue
            duplicate_of = str(target.get("duplicate_of") or "").strip()
            superseded_by = str(target.get("superseded_by") or "").strip()
            if duplicate_of != canonical_provider:
                raise RuntimeError(
                    f"{path.name}:{index} duplicate_of={duplicate_of!r}; expected {canonical_provider!r}"
                )
            if superseded_by and superseded_by != canonical_provider:
                raise RuntimeError(
                    f"{path.name}:{index} superseded_by={superseded_by!r}; expected {canonical_provider!r}"
                )
            evidence.append(f"{path.name}:{index}")
    if not evidence:
        raise RuntimeError(f"No exact duplicate_of mapping found for {duplicate_provider}")
    return evidence


def active_count(cursor: Any, provider: str) -> int:
    cursor.execute(
        "SELECT COUNT(*) FROM courses WHERE provider = %s AND is_active = TRUE",
        (provider,),
    )
    row = cursor.fetchone()
    return int(row[0] if row else 0)


def deactivate_duplicate(
    duplicate_provider: str,
    canonical_provider: str,
    expected_canonical_active: int,
    apply: bool,
) -> dict[str, Any]:
    if not duplicate_provider or not canonical_provider or duplicate_provider == canonical_provider:
        raise ValueError("duplicate and canonical providers must be different non-empty identifiers")
    evidence = duplicate_config_evidence(duplicate_provider, canonical_provider)

    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            canonical_active = active_count(cursor, canonical_provider)
            duplicate_active_before = active_count(cursor, duplicate_provider)
            if canonical_active != expected_canonical_active:
                raise RuntimeError(
                    f"canonical active count changed: expected={expected_canonical_active} actual={canonical_active}"
                )

            deactivated = 0
            if apply and duplicate_active_before:
                cursor.execute(
                    """
                    UPDATE courses
                       SET is_active = FALSE,
                           removed_at = COALESCE(removed_at, CURRENT_TIMESTAMP),
                           updated_at = CURRENT_TIMESTAMP
                     WHERE provider = %s
                       AND is_active = TRUE
                    """,
                    (duplicate_provider,),
                )
                deactivated = int(cursor.rowcount or 0)
            duplicate_active_after = (
                active_count(cursor, duplicate_provider) if apply else duplicate_active_before
            )

        if apply:
            connection.commit()
        else:
            connection.rollback()
        return {
            "applied": apply,
            "duplicate_provider": duplicate_provider,
            "canonical_provider": canonical_provider,
            "config_evidence": evidence,
            "canonical_active": canonical_active,
            "duplicate_active_before": duplicate_active_before,
            "deactivated": deactivated,
            "duplicate_active_after": duplicate_active_after,
            "recoverable": True,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Soft-stale one configured duplicate provider after exact canonical-count verification."
    )
    parser.add_argument("--duplicate-provider", required=True)
    parser.add_argument("--canonical-provider", required=True)
    parser.add_argument("--expected-canonical-active", required=True, type=int)
    parser.add_argument("--apply", action="store_true", help="Apply the reversible is_active=false update.")
    args = parser.parse_args()
    result = deactivate_duplicate(
        duplicate_provider=args.duplicate_provider,
        canonical_provider=args.canonical_provider,
        expected_canonical_active=args.expected_canonical_active,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
