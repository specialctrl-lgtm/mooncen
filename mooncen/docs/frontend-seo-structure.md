# Frontend SEO Structure

Updated: 2026-06-05

## Scope

This document covers the SEO structure added to `frontend2`.

## Implemented

- Replaced the damaged `frontend2/index.html` title/meta block with valid Korean UTF-8 HTML.
- Added base search metadata:
  - `title`
  - `description`
  - `robots`
  - `canonical`
  - Open Graph
  - Twitter card
- Added JSON-LD:
  - `Organization`
  - `WebSite`
  - selected course `Course` while the detail modal is open
- Added crawler files:
  - `frontend2/public/robots.txt`
  - `frontend2/public/sitemap.xml`
- Added `src/seo.ts` to update title, description, canonical, social metadata, and course JSON-LD at runtime.
- Added SEO-friendly direct course URLs with `/course/{course_id}/{course-title-slug}`. Opening a course detail updates the URL, and direct access fetches `/api/courses/{course_id}` before opening the detail modal.
- Legacy `?course={course_id}` URLs are still accepted and are replaced with the SEO path after the course is loaded.
- Added backend-rendered SEO pages for `/course/{course_id}/{course-title-slug}`. These pages return HTML directly from FastAPI with canonical, Open Graph, Twitter metadata, Course JSON-LD, and crawlable course body text.
- Added backend-rendered category SEO pages for `/category/{category-slug}`. These pages return crawlable category summaries and representative course links.
- Added backend-rendered branch SEO pages for `/branch/{branch_id}/{branch-slug}`. These pages return crawlable branch information, address, course count, category tags, and representative course links.
- Added page-specific meta tags for root, course, category, and branch pages:
  - `description`
  - `keywords`
  - `robots`
  - `googlebot`
  - `canonical`
  - Open Graph title/description/url/image
  - Twitter title/description/image
  - Course, Event, CollectionPage, WebSite JSON-LD
- Course detail JSON-LD is emitted as an `@graph` containing both:
  - `Course`
  - `Event`
  This allows course pages with schedule data to be interpreted as event-like class sessions by search engines.
- Production nginx routes `/api/`, `/course/`, `/category/`, and `/branch/` to the API server. JSON API routes are canonical under `/api/*`; legacy `/courses/`, `/branches/providers`, `/branches/nearby`, `/auth/*`, and `/users/*` requests redirect to `/api/...`.
- Added `tools/generate_frontend_sitemap.py` to generate sitemap URLs from active DB courses, categories, and branches.
- Added `VITE_SITE_URL` support for deployment.

## Deployment Notes

`deploy/ubuntu/setup_project.sh` writes:

```bash
VITE_SITE_URL=https://${DOMAIN}
```

For production, `DOMAIN` should be `mooncen.kr`.

After deployment, verify:

```bash
curl -I https://mooncen.kr/
curl -I https://mooncen.kr/course/<course_uuid>/<course-title-slug>
curl -I https://mooncen.kr/category/<category-slug>
curl -I https://mooncen.kr/branch/<branch_uuid>/<branch-slug>
curl https://mooncen.kr/robots.txt
curl https://mooncen.kr/sitemap.xml
```

Regenerate sitemap manually:

```bash
python -X utf8 tools/generate_frontend_sitemap.py --site-url https://mooncen.kr
mooncenctl sitemap
```

The deploy script runs sitemap generation after DB migration:

```bash
python -X utf8 tools/generate_frontend_sitemap.py --site-url "https://${DOMAIN}"
```

Generated URL shape:

```text
https://mooncen.kr/
https://mooncen.kr/category/<category-slug>
https://mooncen.kr/branch/<branch_uuid>/<branch-slug>
https://mooncen.kr/course/<course_uuid>/<course-title-slug>
```

## Current Limitation

The root app is still a client-side rendered SPA. Course, category, and branch URLs are backend-rendered SEO pages, so their first-response HTML contains crawlable content.

For stronger indexing of individual courses, add one of these later:

- A crawler-friendly HTML snapshot endpoint for course detail pages.
