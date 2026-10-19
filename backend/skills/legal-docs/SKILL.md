---
name: legal-docs
description: Draft a privacy policy, terms, cookie notice and imprint that match what this product actually collects (data_collection_scan), checked against current GDPR / ePrivacy / CCPA rules, and put them where the site serves them.
---

# Legal documents

A privacy policy copied from a template and not matching the product is worse than none: it is
a written statement that is false. Every document starts from the facts of this project.

## 1. Collect the facts

1. `data_collection_scan` the project: form fields, third parties, cookies, packages, keys.
2. `read_file BLUEPRINT.md` (who the product is for, which countries, payments, accounts).
3. If the site runs, open it (`browser_navigate` APP_URL, `browser_snapshot`) and look at the
   forms and the footer: what a visitor actually sees and submits.
4. Facts you cannot find (company name, address, registration number, contact email, data
   protection officer) are placeholders in `[BRACKETS]`, listed at the end of your reply. Never
   invent a company, an address or a registration number.

## 2. Check the rules that apply

- EU / EEA visitors: GDPR (Art. 13 information, legal bases in Art. 6, data subject rights in
  Art. 15-22) and the ePrivacy rules on cookies (consent before non-essential cookies load).
- California consumers: CCPA / CPRA ("Do Not Sell or Share" when data is sold or shared for ads).
- Germany / Austria: an Impressum (imprint) is required for commercial sites.
- `web_search` + `web_fetch` the regulator's own page when a point is unclear or may have
  changed (EDPB, ICO, the national DPA, oag.ca.gov). Quote the URL you relied on.

## 3. Write the documents

Each one in plain language, short sections, dated ("Last updated: <date>"):

- **Privacy policy**: who is responsible (controller and contact), what data, for what purpose,
  legal basis per purpose, who receives it (every third party from the scan, by name and
  country, transfers outside the EU and the safeguard), how long it is kept, the visitor's
  rights and how to use them, the right to complain to a supervisory authority, cookies (or a
  link to the cookie notice).
- **Cookie notice**: a table - name or provider, purpose, essential or not, lifetime. If the
  scan found analytics, pixels, embeds or Google Fonts loaded from Google, say a consent banner
  is required before they load, and tell Pulse in your reply (or self-host the fonts).
- **Terms of service** (when there are accounts, payments or user content): what the service
  is, accounts, payments and refunds, acceptable use, user content licence, liability limits
  within what consumer law allows, termination, governing law, changes.
- **Imprint** when the site is commercial in DE / AT, or the brief asks.

## 4. Put them where the site serves them

- Write Markdown drafts to `legal/` (for example `legal/privacy-policy.md`). If the project is
  a site and the developer phase has run, also add the pages the same way the other pages are
  built (a Blade view plus a route in Laravel) and link them from the footer, without touching
  application logic.
- Do not change source code beyond those pages and footer links.

## Reply

The files you wrote, the risks you found (missing consent banner, data sent outside the EU
without a safeguard, a form collecting more than it needs), and the placeholders the owner must
fill in. Close with: these drafts are not a substitute for review by a qualified lawyer.
