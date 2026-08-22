from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.db_utils import get_db_cursor
from utils.seo_quality import MIN_CATEGORY_COURSES, is_indexable_category_value


DEFAULT_SITE_URL = "https://mooncen.kr"
DEFAULT_LIMIT = 47000
SITEMAP_URL_LIMIT = 50000
def normalize_site_url(value: str) -> str:
    text = (value or DEFAULT_SITE_URL).strip().rstrip("/")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", text) and not text.startswith(("http://", "https://")):
        raise ValueError("site URL must use HTTP or HTTPS")
    if not text.startswith(("http://", "https://")):
        text = f"https://{text}"
    parsed = urlsplit(text)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("site URL must be an absolute HTTP(S) origin without credentials, query, or fragment")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def slugify(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = re.sub(r"[^\w가-힣]+", "-", text, flags=re.UNICODE)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:80] or "page"


def course_url_from_row(site_url: str, row: dict[str, Any]) -> str:
    slug = slugify(" ".join(str(row.get(key) or "") for key in ("title", "branch_name") if row.get(key)))
    return f"{site_url}/course/{quote(str(row['id']), safe='')}/{quote(slug, safe='')}"


def branch_url_from_row(site_url: str, row: dict[str, Any]) -> str:
    provider = str(row.get("provider") or "").replace("_", " ").title()
    slug = slugify(" ".join(part for part in (provider, str(row.get("name") or "")) if part))
    return f"{site_url}/branch/{quote(str(row['id']), safe='')}/{quote(slug, safe='')}"


def category_url_from_row(site_url: str, row: dict[str, Any]) -> str:
    return f"{site_url}/category/{quote(slugify(row.get('category')), safe='')}"


def is_indexable_category(row: dict[str, Any]) -> bool:
    return is_indexable_category_value(row.get("category"), row.get("course_count"))


def xml_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value:
        return str(value)[:10]
    return date.today().isoformat()


def sitemap_url(loc: str, lastmod: str, changefreq: str, priority: str) -> str:
    return (
        "  <url>\n"
        f"    <loc>{escape(loc)}</loc>\n"
        f"    <lastmod>{escape(lastmod)}</lastmod>\n"
        f"    <changefreq>{escape(changefreq)}</changefreq>\n"
        f"    <priority>{escape(priority)}</priority>\n"
        "  </url>"
    )


def active_course_where() -> str:
    return """
        is_active IS TRUE
        AND title IS NOT NULL
        AND (end_date IS NULL OR end_date >= CURRENT_DATE)
    """


def fetch_active_courses(limit: int) -> list[dict[str, Any]]:
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                courses.id,
                courses.title,
                b.name AS branch_name,
                courses.updated_at,
                courses.last_seen_at,
                courses.start_date,
                courses.end_date,
                courses.status
            FROM courses
            LEFT JOIN branches b ON b.id = courses.branch_id
            WHERE {active_course_where()}
            ORDER BY
                CASE WHEN courses.status IN ('OPEN', 'SCHEDULED', 'WAITING', 'DEADLINE') THEN 0 ELSE 1 END,
                COALESCE(courses.updated_at, courses.last_seen_at, courses.first_seen_at) DESC NULLS LAST,
                courses.start_date DESC NULLS LAST
            LIMIT %(limit)s
            """,
            {"limit": limit},
        )
        return list(cursor.fetchall())


def fetch_active_categories() -> list[dict[str, Any]]:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT
                COALESCE(
                    NULLIF(standard_category_label, ''),
                    NULLIF(collection_category, ''),
                    NULLIF(service_group, ''),
                    NULLIF(domain_category, ''),
                    NULLIF(ai_category, '')
                ) AS category,
                COUNT(*) AS course_count,
                MAX(COALESCE(updated_at, last_seen_at, first_seen_at)) AS updated_at
            FROM courses
            WHERE is_active IS TRUE
              AND title IS NOT NULL
              AND (end_date IS NULL OR end_date >= CURRENT_DATE)
            GROUP BY 1
            HAVING COALESCE(
                NULLIF(standard_category_label, ''),
                NULLIF(collection_category, ''),
                NULLIF(service_group, ''),
                NULLIF(domain_category, ''),
                NULLIF(ai_category, '')
            ) IS NOT NULL
              AND COUNT(*) >= %(min_course_count)s
            ORDER BY course_count DESC, category ASC
            """,
            {"min_course_count": MIN_CATEGORY_COURSES},
        )
        return [row for row in cursor.fetchall() if is_indexable_category(row)]


def fetch_active_branches() -> list[dict[str, Any]]:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT
                b.id,
                b.provider,
                b.name,
                b.updated_at,
                COUNT(c.id) AS course_count,
                MAX(COALESCE(c.updated_at, c.last_seen_at, c.first_seen_at)) AS latest_course_at
            FROM branches b
            JOIN courses c ON c.branch_id = b.id
            WHERE c.is_active IS TRUE
              AND c.title IS NOT NULL
              AND (c.end_date IS NULL OR c.end_date >= CURRENT_DATE)
            GROUP BY b.id, b.provider, b.name, b.updated_at
            ORDER BY course_count DESC, b.provider ASC, b.name ASC
            """
        )
        return list(cursor.fetchall())


def build_sitemap(
    site_url: str,
    courses: list[dict[str, Any]],
    categories: list[dict[str, Any]],
    branches: list[dict[str, Any]],
) -> str:
    today = date.today().isoformat()
    urls = [sitemap_url(f"{site_url}/", today, "daily", "1.0")]
    for row in categories:
        if not is_indexable_category(row):
            continue
        urls.append(sitemap_url(category_url_from_row(site_url, row), xml_date(row.get("updated_at")), "daily", "0.9"))
    for row in branches:
        lastmod = xml_date(row.get("latest_course_at") or row.get("updated_at"))
        urls.append(sitemap_url(branch_url_from_row(site_url, row), lastmod, "daily", "0.85"))
    for row in courses:
        lastmod = xml_date(row.get("updated_at") or row.get("last_seen_at") or row.get("start_date"))
        status = str(row.get("status") or "")
        priority = "0.8" if status in {"OPEN", "SCHEDULED", "WAITING", "DEADLINE"} else "0.5"
        urls.append(sitemap_url(course_url_from_row(site_url, row), lastmod, "daily", priority))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )


def write_outputs(xml: str, outputs: list[Path]) -> list[Path]:
    written: list[Path] = []
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(xml, encoding="utf-8")
        written.append(output)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate frontend sitemap.xml from active DB SEO pages.")
    parser.add_argument("--site-url", default=DEFAULT_SITE_URL, help="Canonical site URL, e.g. https://mooncen.kr")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Maximum course URLs. Keep total below 50,000.")
    parser.add_argument(
        "--output",
        action="append",
        default=[],
        help="Output sitemap path. Can be repeated. Defaults to frontend2 public and dist sitemap.xml.",
    )
    args = parser.parse_args()

    site_url = normalize_site_url(args.site_url)
    requested_limit = max(0, min(args.limit, DEFAULT_LIMIT))
    categories = fetch_active_categories()
    branches = fetch_active_branches()
    course_limit = max(0, min(requested_limit, SITEMAP_URL_LIMIT - 1 - len(categories) - len(branches)))
    courses = fetch_active_courses(course_limit)
    xml = build_sitemap(site_url, courses, categories, branches)
    outputs = [Path(path) for path in args.output] or [
        ROOT / "frontend2" / "public" / "sitemap.xml",
        ROOT / "frontend2" / "dist" / "sitemap.xml",
    ]
    written = write_outputs(xml, outputs)
    print(f"site_url={site_url}")
    print(f"category_urls={len(categories)}")
    print(f"branch_urls={len(branches)}")
    print(f"course_urls={len(courses)}")
    print(f"total_urls={1 + len(categories) + len(branches) + len(courses)}")
    for path in written:
        print(f"wrote={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
