---
name: design-md
description: Keep the project's look in DESIGN.md (tokens + rules, Google Labs format) and build every page from it, so the UI is consistent and not generic.
---

# DESIGN.md

`DESIGN.md` at the project root is the design system written for agents: machine-readable
tokens in YAML front matter, then short prose saying how to use them. Whoever builds or
checks UI reads it first. If it is missing, the designer (Pixel) writes it before styling
anything; the developer may write a minimal one when there is no designer phase.

## Format

```markdown
---
name: Remonti Naydenov
colors:
  primary: "#1F4E79"      # brand, primary buttons, links
  accent: "#F2A900"       # calls to action, highlights
  surface: "#FFFFFF"
  surface-muted: "#F4F6F8"
  text: "#1C2430"
  text-muted: "#5B6675"
  border: "#DDE2E8"
  danger: "#C62828"
typography:
  font-sans: "Inter, system-ui, sans-serif"
  display: { fontSize: 48, fontWeight: 700, lineHeight: 1.1 }
  h2: { fontSize: 32, fontWeight: 700, lineHeight: 1.2 }
  body: { fontSize: 16, fontWeight: 400, lineHeight: 1.6 }
spacing:
  scale: [4, 8, 12, 16, 24, 32, 48, 64, 96]
rounded:
  sm: 6
  md: 10
  lg: 16
components:
  button-primary: { bg: primary, text: surface, rounded: md, paddingX: 20, paddingY: 12 }
  card: { bg: surface, border: border, rounded: lg, padding: 24 }
---

## Overview
Who the site is for and the feeling it should give (one paragraph).

## Colors
What each color is for. Contrast: body text on surface at least 4.5:1.

## Typography
Which style for which element. One font family, two at most.

## Layout
Max content width, grid, section spacing, breakpoints (mobile first).

## Elevation & Depth
When to use a shadow or border.

## Shapes
Radius per element.

## Components
Header, footer, buttons, cards, forms, gallery grid, admin tables: how each looks and behaves.

## Do's and Don'ts
Concrete rules, e.g. "one primary button per section", "no lorem ipsum in production pages".
```

## Using it with Tailwind v4

Turn the tokens into theme variables once, in `resources/css/app.css` (or the main CSS file):

```css
@import "tailwindcss";

@theme {
  --color-primary: #1F4E79;
  --color-accent: #F2A900;
  --color-surface-muted: #F4F6F8;
  --font-sans: "Inter", system-ui, sans-serif;
  --radius-card: 16px;
}
```

Then use the utilities (`bg-primary`, `text-accent`, `rounded-card`) everywhere. No raw hex
values in views; a new color goes into DESIGN.md and `@theme` first.

## Checklist for the designer

1. Read the brief and the existing views. Write `DESIGN.md` (tokens + every section above).
2. Map tokens to `@theme`; build shared Blade/React components from the Components section.
3. Style every page from those components; rebuild the assets.
4. Check mobile (375 px) and desktop (1280 px) with the browser tools; fix overflow and contrast.

## Checklist for QA

Compare pages against DESIGN.md: colors only from the tokens, headings use the type scale,
buttons and cards look like the Components section, and nothing breaks at 375 px.
