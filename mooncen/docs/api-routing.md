# API Routing

Updated: 2026-06-26

MoonCen separates user-facing routes from JSON API routes.

## Canonical JSON API

All JSON API endpoints should live under `/api/*`.

```text
/api/courses
/api/branches
/api/auth
/api/users/me
/api/user-courses
/api/ops
```

Frontend code should call only `/api/*` for JSON. Vite and production Nginx both proxy `/api/` to FastAPI.

## User And SEO Routes

User-facing routes stay outside `/api`.

```text
/
/courses
/branches
/course/{course_id}/{slug}
/branch/{branch_id}/{slug}
/category/{slug}
```

`/course/`, `/branch/`, and `/category/` are backend-rendered SEO HTML routes. The SPA can own `/courses` and `/branches` page routes without colliding with JSON APIs.

## Legacy Compatibility

FastAPI still registers the old JSON routes outside `/api` with `include_in_schema=False` for temporary compatibility.

Production Nginx redirects known legacy API routes to `/api/...`:

```text
/courses/*          -> /api/courses/*
/branches/providers -> /api/branches/providers
/branches/nearby    -> /api/branches/nearby
/auth/* API paths    -> /api/auth/*
/users/*            -> /api/users/*
```

New code and tests should not use those legacy paths.
