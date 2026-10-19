---
name: task-blueprint
description: Turn a raw brief into BLUEPRINT.md - goals, numbered testable features, verbatim content, a checked stack, assumptions and per-cat notes - the file every later phase reads.
---

# Task blueprint

You run first. Everyone after you sees only a short excerpt of your reply and is told to
`read_file BLUEPRINT.md` for the detail, so the file is the deliverable, not the chat message.
Write it at the project root with `write_file`, then reply with a five-line summary.

## Before you write

1. `list_dir` the project. An existing app (`artisan`, `package.json`, `composer.json`) is the
   stack: read its manifest and note the framework version. Never plan a rewrite of a project
   the brief only wants extended.
2. A stack the brief names but the folder does not have: check it is real and current with
   `docs_search` / `web_search` (one or two lookups, see `docs-lookup`). Name the exact major
   version in the blueprint.
3. Do not ask the user questions the brief already answers. When something is missing, pick a
   sensible default and list it under Assumptions. Only a choice that would waste the whole
   build if wrong (the stack, a payment provider) is worth `ask_user`.

## BLUEPRINT.md

```markdown
# <Product name>: blueprint

## Goal
One or two sentences: who it is for and what they can do with it.

## Features
1. <Page or capability> - <what a visitor or admin can do; the observable result>
2. ...
(Numbered, one line each, each one testable: QA writes a test per line.)

## Content (verbatim)
Every text, section, service, price, address the brief gives, copied exactly. The developer
pastes these into the pages; never paraphrase them.

## Stack
Framework + major version, database, CSS approach, hosting target. "Existing project" when the
folder already has one.

## Constraints
Languages of the UI, accessibility, performance, browsers, deadlines, budget, brand colours.

## Out of scope
What the brief does not ask for (so nobody builds it).

## Assumptions
- <the default you picked> (because the brief did not say)

## Notes per cat
- Vanguard: shape of the data, the admin area, anything that decides the architecture.
- Forge: environment facts (database, mail, queues, storage, the URL).
- Pulse: integrations and their exact names (Stripe Checkout, not "payments").
- Pixel: the look the brief asks for, references, brand colours.
- Aegis: the flows that must work end to end.
- Lex: legal pages needed (privacy policy, terms, cookies, imprint) - only if the product
  collects personal data, takes payments or the brief says so.
- Blaze: audience, tone, SEO keywords, launch copy - only if the brief cares about marketing.
```

## Rules

- Features are behaviours, not files. "Admin can upload a gallery photo and see it on /gallery",
  not "GalleryController".
- Keep it under about 150 lines. Detail belongs to Vanguard's architecture.
- Do not design the system or write code.
