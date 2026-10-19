---
name: security-checks
description: The security checks to run before a site ships - dependency audits, debug and secret leaks, auth on admin routes, CSRF, uploads, headers - with the exact commands for Laravel and Node.
---

# Security checks

Not a penetration test: the handful of mistakes that sink most small sites, checked in a few
minutes with commands, and reported like any other failure (file, command, output).

## 1. Dependencies

- PHP: `composer audit` (Composer 2.4+). A high or critical advisory is a FAIL.
- Node: `npm audit --omit=dev`. Report high and critical only; dev-only advisories are noise.
- Python: `pip-audit` if it is installed; otherwise skip and say so.
- Fix by updating the package within the same major version (`composer update vendor/pkg`,
  `npm update pkg`). Never `npm audit fix --force`: it jumps majors and breaks the build.

## 2. Secrets and debug

- `.env` must be in `.gitignore`, and `git ls-files .env` must print nothing.
- `search_files` for hard-coded keys in source: `sk_live_`, `sk_test_`, `AKIA`, `-----BEGIN`,
  `password =`, `api_key`. A key in a tracked file is a FAIL even on a test key.
- Production settings: Laravel `APP_DEBUG=false` and `APP_ENV=production` on the server, a set
  `APP_KEY`. A page that shows a stack trace (Ignition / Whoops) to a guest is a FAIL.
- Public folder: nothing but the front controller and built assets. `/.env`, `/.git/config`,
  `/storage/logs/laravel.log` must answer 403 or 404 (`http_endpoint_audit` each one).

## 3. Access control

- Every admin route sits behind `auth` (and a role or gate when there are user roles):
  `php artisan route:list --path=admin` shows the middleware column. A guest request to each
  admin URL must redirect to the login (feature test, see `automated-testing`).
- Mass assignment: models use `$fillable` (or guarded) - never `$guarded = []` with
  `$request->all()`.
- Queries take bound parameters. `DB::raw` / `whereRaw` with request input concatenated in is a
  FAIL.

## 4. Forms and uploads

- Every POST form carries `@csrf` (Laravel adds the check; a 419 in a test means it is missing).
- Login and contact forms are rate limited (`throttle` middleware).
- Uploads validate type and size (`mimes:jpg,png,webp|max:4096`), are stored with a generated
  name on the `public` disk or outside the web root, and are never executed: no `.php` in the
  allowed list.
- Output is escaped: Blade `{{ }}`, never `{!! !!}` on user input; React never
  `dangerouslySetInnerHTML` with user input.

## 5. Transport and headers (deployed site only)

- HTTPS with a redirect from HTTP; cookies `Secure` and `HttpOnly` (`SESSION_SECURE_COOKIE=true`).
- `http_endpoint_audit` the home page and note missing `Strict-Transport-Security`,
  `X-Content-Type-Options`, `X-Frame-Options` / `frame-ancestors`, `Referrer-Policy`. Missing
  headers are a recommendation, not a FAIL.

## Reporting

One line per finding: severity (FAIL / recommendation), what, where (file:line or URL), the fix.
Say which checks you skipped and why (no composer, not deployed yet).
