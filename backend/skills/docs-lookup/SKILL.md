---
name: docs-lookup
description: Look APIs, commands and package support up in the official docs (Laravel, PHP, Tailwind, JavaScript/MDN, Vite...) before writing code or installing anything.
---

# Docs lookup

Your memory of a framework is a year or more old, and frameworks move: packages get retired,
config files change shape, commands get renamed. Writing code from memory against the wrong
version is the most common reason a build "succeeds" and the site is still broken.

## When to look something up

- Before adding a package: does it support the version this project runs? (Example: Jetstream
  and Breeze are no longer Laravel's starter kits since 12.x.)
- Before using an API, config key, Artisan / npm command or directive you are not sure of.
- When a command fails with an error you do not recognise: search the error text.
- When the brief names a library you have not used in this project yet.

## How

1. Know the version first: `composer.json` / `composer.lock` / `package.json`, or
   `php artisan --version`. `docs_search` tells you the Laravel version for you.
2. `docs_search` with a short query and the `source`
   (laravel, livewire, php, tailwind, javascript, vite, alpine, react, vue, node, ...).
   It searches only that project's official site.
3. `web_fetch` the best page. Long pages: continue with `start_index`. Copy the exact
   syntax from the page for the version you run.
4. If a Context7 tool is available (`mcp__context7__resolve-library-id`, then
   `mcp__context7__query-docs`), use it for version-specific snippets: resolve the library
   once, then ask a focused question. At most three calls per question.
5. Nothing official? `web_search` with the error text or the question, then `web_fetch`
   the most credible answer. Prefer the vendor's docs, then its GitHub issues.

## Official sites

| Topic | Docs |
|---|---|
| Laravel | https://laravel.com/docs/{major}.x (e.g. /docs/13.x) |
| Livewire | https://livewire.laravel.com/docs |
| PHP | https://www.php.net/manual/en/ |
| Composer | https://getcomposer.org/doc/ |
| Tailwind CSS | https://tailwindcss.com/docs |
| JavaScript, HTML, CSS, Web APIs | https://developer.mozilla.org/en-US/docs/Web |
| Vite | https://vite.dev/guide/ |
| Alpine.js | https://alpinejs.dev |
| React / Vue / Inertia | https://react.dev, https://vuejs.org/guide, https://inertiajs.com |

## Rules

- Never `composer require` / `npm install` a package you have not checked against the
  installed framework version.
- Quote the doc URL you used in your reply when a decision depends on it.
- Do not spend more than about four lookups on one question: decide and build.
