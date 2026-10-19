---
name: seo-copy
description: Write the site's copy and search metadata - positioning, page copy in COPY.md, titles, descriptions, Open Graph, robots.txt and sitemap - and verify each page with seo_page_audit.
---

# SEO and copy

You write what the outside world reads: the words on the pages, what a search result shows, what
a shared link looks like. Write it once, in files the developer can paste from, then check the
real pages.

## 1. Understand the product and the audience

- `read_file BLUEPRINT.md` (goal, audience, verbatim content). Content the brief gives verbatim
  stays verbatim: you write around it, you never rewrite it.
- `web_search` the product category and two or three competitors: what words do they rank and
  advertise with, what do they leave out? Pick 3-5 keyword phrases a real customer would type
  (local intent counts: "dentist Sofia centre").

## 2. Write COPY.md

At the project root, one section per page:

```markdown
## / (home)
title: <50-60 chars, main keyword first, brand last>
description: <140-160 chars, the benefit plus a call to action>
h1: <one, the promise in plain words>
sections:
- <heading> - <2-3 sentences>
cta: <button label>
```

Then a **Voice** section: three adjectives, words to use, words to avoid.

## 3. Metadata the developer adds (or you add, in the views)

- One `<title>` and `<meta name="description">` per page, from COPY.md.
- `<html lang="...">`, `<link rel="canonical">` with the absolute URL.
- Open Graph and Twitter: `og:title`, `og:description`, `og:image` (1200x630), `og:type`,
  `twitter:card=summary_large_image`.
- Exactly one H1 per page; H2s for sections; alt text on every meaningful image.
- `robots.txt` (allow everything, point to the sitemap) and `sitemap.xml` with every public
  page (Laravel: a route that returns XML, or `spatie/laravel-sitemap` if it supports the
  installed version - check first, see `docs-lookup`).
- A local business: JSON-LD `LocalBusiness` with name, address, phone, opening hours.
- Laravel: put these in the layout from a `@section('title')` / `@section('description')` pair,
  so each view sets its own. You may edit views and the layout for copy and metadata; leave
  controllers, models and routes (except the sitemap) to the developer.

## 4. Verify

`seo_page_audit` every public page on the real URL (APP_URL in `.env`). Fix what it lists, or say
who must. A page that comes back nearly empty renders only with JavaScript: tell Vanguard,
because search engines will see that same empty page.

## Launch material (when asked)

Press release (headline, dateline, the news in the first sentence, one quote, boilerplate,
contact), three social posts per channel in the brand voice, and a short email announcement.
Nothing is published or sent without the owner's approval.
