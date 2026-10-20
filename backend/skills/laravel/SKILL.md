---
name: laravel
description: Build, set up and verify a Laravel site (Blade + Tailwind, models, migrations, seeders, admin area, uploads), including .env, database, assets and caches.
---

# Laravel projects

Read the project before you plan: `composer.json` (framework version), `routes/web.php`,
`app/`, `database/migrations/`, `resources/views/`, `vite.config.js`, `.env`. Build on what
is there. Never re-scaffold a folder that already has `artisan`.

## Versions and packages

- Find the version: `php artisan --version`. Docs: https://laravel.com/docs/{major}.x.
- Since Laravel 12, Breeze and Jetstream are no longer the starter kits and may not support
  the newest release (check before installing). New apps choose a starter kit at
  `laravel new` (React, Vue, Svelte, Livewire; auth through Fortify). In an existing app,
  a small admin login is plain Laravel: a `LoginController` with `Auth::attempt()`,
  `auth` middleware on the admin route group, `Auth::logout()` on POST /logout.
  Check with `docs_search` before adding any auth package.
- Tailwind v4 is the default: `@import "tailwindcss";` in `resources/css/app.css` and the
  `@tailwindcss/vite` plugin in `vite.config.js`. No `tailwind.config.js` is needed.
  Blade pages load assets with `@vite(['resources/css/app.css', 'resources/js/app.js'])`.

## Structure (clean, reusable)

- One layout component (`resources/views/components/layouts/app.blade.php` or
  `layouts/app.blade.php`) with header, nav and footer. Pages extend it.
- Repeated UI in Blade components (`resources/views/components/*.blade.php`): service
  card, section heading, gallery tile, form field, flash message.
- Content that is a list (services, sections) lives in one place: a config file
  (`config/site.php`), a seeder-filled table, or an array the controller passes in. Never
  copy-paste twenty sections by hand.
- Controllers stay thin. Validation in Form Requests (`php artisan make:request`).
  Admin controllers under `App\Http\Controllers\Admin`, routes under
  `Route::prefix('admin')->middleware('auth')->name('admin.')`.
- Models with `$fillable` and relations; migrations with foreign keys
  (`foreignId()->constrained()->cascadeOnDelete()`); factories and seeders.
- Generate files with Artisan, then fill them in:
  `php artisan make:model Gallery -mfs`, `make:controller Admin/GalleryController --resource`.

## Uploads (galleries, images)

- Store on the `public` disk: `$request->file('image')->store('galleries/'.$gallery->id, 'public')`.
- Validate: `'images.*' => ['image', 'max:5120']`.
- Show with `Storage::url($path)` or `asset('storage/'.$path)`.
- Run `php artisan storage:link` once. Without it every uploaded image 404s.
- Delete the file when its row is deleted.

## Seeded admin user

Seed it in `DatabaseSeeder` with `User::updateOrCreate(['email' => ...], [...])` and
`Hash::make()`. Put the credentials in your final reply, not in the page.

## Environment (Laragon on Windows)

- The site is served by Apache at the `.local` / `.test` host Laragon made for the folder
  (`APP_URL` in `.env`), not by `php artisan serve`.
- MySQL: `DB_CONNECTION=mysql`, `DB_HOST=127.0.0.1`, `DB_USERNAME=root`, empty password.
  Create the database first if it is missing. SQLite is fine for a small site:
  `DB_CONNECTION=sqlite` and an existing `database/database.sqlite`.
- After `.env` changes: `php artisan config:clear`.

## Environment (a server account, in a container)

- Commands run in your own container; there is no Apache or Laragon. Serve the site with
  `php artisan serve --host=0.0.0.0 --port=8000` in the background (run it once, then keep working),
  set `APP_URL=http://127.0.0.1:8000`, and use SQLite (`DB_CONNECTION=sqlite`).
- `--host=0.0.0.0` matters: the browser and the checks reach your container from outside it.

## The finish line (run all of these, in this order)

1. `composer install` if `vendor/` is missing; `npm install` if `node_modules/` is missing.
2. `php artisan key:generate` if `APP_KEY` is empty.
3. `php artisan migrate --seed --force` (or `migrate:fresh --seed` on a dev database you own).
4. `php artisan storage:link`
5. `npm run build`
6. `php artisan optimize:clear`
7. `php artisan route:list --except-vendor`: every page in the brief must be listed.
8. Request each page (the browser tools or `http_endpoint_audit` on APP_URL): status 200,
   and the content of the brief is visible. A page that renders only the layout is not done.

## Before you say done (every time you touched a view or PHP file)

- `php artisan view:cache` compiles every Blade view. It does not run them, so also request the
  pages: a view with an `@if` and no `@endif` compiles fine and then fails with
  "syntax error, unexpected end of file, expecting elseif or else or endif" on the first request.
- Every `@if` / `@foreach` / `@forelse` / `@section` / `@push` / `@auth` / `@isset` you open needs
  its closing directive. When you add a block to a view, write the opening and closing lines in the
  same edit.
- `php -l path/to/File.php` for every PHP file you edited.
- `npm run build` when you changed CSS, JS or Tailwind classes in new files.
- The harness runs these checks after your work order too and hands any failure straight back to
  you; fixing it yourself first is faster.

## Common traps

- A blank page with only header and footer: the view has no content, or a section loop
  got an empty array. Read the view and fill it in.
- "Vite manifest not found": run `npm run build`.
- 500 after changing `.env` or routes: `php artisan optimize:clear`, then read
  `storage/logs/laravel.log` (the last 60 lines).
- Images 404: missing `storage:link`, or the URL was built without `Storage::url`.
