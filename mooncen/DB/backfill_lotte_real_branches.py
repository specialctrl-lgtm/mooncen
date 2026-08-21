from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict

import requests
from bs4 import BeautifulSoup
from sqlalchemy import text

sys.path.append(str(Path(__file__).resolve().parents[1]))

from backend.database import SessionLocal


LOTTE_INDEX_URL = "https://culture.lotteshopping.com/index.do"


def normalize_branch_name(value: str) -> str:
    name = value.strip()
    name = name.replace("롯데문화센터", "")
    name = name.replace(" ", "")
    if name.endswith("점"):
        name = name[:-1]
    return name


def fetch_lotte_branch_map() -> Dict[str, str]:
    response = requests.get(
        LOTTE_INDEX_URL,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    branches: Dict[str, str] = {}
    for link in soup.select('a[href*="brchCd="], a[data-brch-cd]'):
        code = link.get("data-brch-cd")
        if not code:
            match = re.search(r"brchCd=([^&]+)", link.get("href", ""))
            code = match.group(1) if match else None
        name = link.get_text(" ", strip=True)
        if code and name and code.isdigit():
            branches[code] = name
    return branches


def main() -> None:
    branch_map = fetch_lotte_branch_map()
    if not branch_map:
        raise RuntimeError("No LOTTE branches found from index page")

    db = SessionLocal()
    try:
        existing = db.execute(
            text(
                """
                SELECT id, branch_code, name, address, phone, lat, lon
                FROM branches
                WHERE provider = 'LOTTE'
                """
            )
        ).mappings().all()
        by_name = {normalize_branch_name(row["name"]): row for row in existing}

        created_or_updated = 0
        for code, source_name in branch_map.items():
            normalized = normalize_branch_name(source_name)
            old = by_name.get(normalized)
            display_name = source_name if source_name.startswith("롯데문화센터") else f"롯데문화센터 {source_name}"

            db.execute(
                text(
                    """
                    INSERT INTO branches (provider, branch_code, name, address, phone, lat, lon)
                    VALUES ('LOTTE', :code, :name, :address, :phone, :lat, :lon)
                    ON CONFLICT (provider, branch_code)
                    DO UPDATE SET
                        name = EXCLUDED.name,
                        address = COALESCE(NULLIF(EXCLUDED.address, ''), branches.address),
                        phone = COALESCE(NULLIF(EXCLUDED.phone, ''), branches.phone),
                        lat = COALESCE(EXCLUDED.lat, branches.lat),
                        lon = COALESCE(EXCLUDED.lon, branches.lon)
                    """
                ),
                {
                    "code": code,
                    "name": display_name,
                    "address": old["address"] if old else "",
                    "phone": old["phone"] if old else "",
                    "lat": old["lat"] if old else None,
                    "lon": old["lon"] if old else None,
                },
            )
            created_or_updated += 1

        updated = db.execute(
            text(
                """
                WITH course_branch AS (
                    SELECT
                        c.id AS course_id,
                        b.id AS new_branch_id
                    FROM courses c
                    JOIN branches b
                      ON b.provider = 'LOTTE'
                     AND b.branch_code = substring(c.raw_url from 'brchCd=([^&]+)')
                    WHERE c.provider = 'LOTTE'
                      AND c.raw_url LIKE '%brchCd=%'
                )
                UPDATE courses c
                SET branch_id = cb.new_branch_id
                FROM course_branch cb
                WHERE c.id = cb.course_id
                  AND c.branch_id IS DISTINCT FROM cb.new_branch_id
                """
            )
        ).rowcount

        db.commit()
        print(f"LOTTE branches upserted: {created_or_updated}")
        print(f"LOTTE courses relinked: {updated}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
