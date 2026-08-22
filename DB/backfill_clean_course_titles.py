from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

sys.path.append(str(Path(__file__).resolve().parents[1]))

from backend.database import SessionLocal
from title_cleaner import clean_course_title


def main() -> None:
    db = SessionLocal()
    try:
        db.execute(text("ALTER TABLE courses ADD COLUMN IF NOT EXISTS title_raw VARCHAR(255)"))
        db.execute(text("ALTER TABLE courses ADD COLUMN IF NOT EXISTS title_prefix_removed TEXT"))
        db.execute(text("UPDATE courses SET title_raw = title WHERE title_raw IS NULL"))

        rows = db.execute(
            text(
                """
                SELECT id, provider, title_raw
                FROM courses
                WHERE title_raw IS NOT NULL
                ORDER BY provider, title_raw
                """
            )
        ).mappings().all()

        changed = 0
        samples = []
        for row in rows:
            clean_title, removed = clean_course_title(row["title_raw"])
            if clean_title != row["title_raw"]:
                changed += 1
                if len(samples) < 30:
                    samples.append((row["provider"], row["title_raw"], clean_title, removed))

            db.execute(
                text(
                    """
                    UPDATE courses
                    SET title = :title,
                        title_prefix_removed = :removed
                    WHERE id = :id
                    """
                ),
                {
                    "id": row["id"],
                    "title": clean_title,
                    "removed": removed or None,
                },
            )

        db.commit()

        print(f"courses scanned: {len(rows)}")
        print(f"titles changed: {changed}")
        print("\nSamples:")
        for provider, before, after, removed in samples:
            print(f"[{provider}]")
            print(f"  before : {before}")
            print(f"  after  : {after}")
            print(f"  removed: {removed}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
