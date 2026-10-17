---
name: web-research
description: Answer current facts with web_search then web_fetch, not a list of websites.
---

# Web research

1. `web_search` with a specific query (include city/date when it matters). No extra Docker service is required.
2. If snippets lack the actual numbers or quotes, `web_fetch` one or two URLs (official docs, README, API pages). Long pages: use `start_index` to continue.
3. Answer the question. Cite hosts as markdown links. Never dump a directory of sites to go visit.
4. Weather: use the forecast snapshot in the search result when present.
5. Existing photos (a person, place, product, screenshot already on the web): `image_search`. Do not use `generate_image` for that.
6. Answer in English.
