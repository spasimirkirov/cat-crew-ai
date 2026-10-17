---
name: jinx-ui
description: >-
  Visual language for the Jinx AI PyQt6 desktop app. Use when changing UI, QSS,
  titlebar, overlay, Settings (General / API Access / Connectors / Skills / Appearance), the Models tab
  (General / Local / Hugging Face Hub / Cloud API / ComfyUI), scrollbars,
  chat chrome, or reusable widgets. Dark acrylic Apple-like UX with cyan/pink
  Jinx accents.
---

# Jinx UI

Dark, quiet, Apple-like. Cyan `#22d3ee` and pink `#f472b6` as sparks, not as walls of purple.

## Must

- Reuse `jinx_ai/widgets.py`: `PageHeader`, `Pill`, `chip_button`, `GhostButton`, `HudButton`, `TrafficLights`, `SwitchRow`, `FlowLayout`. Compact on/off chrome uses `IconToggle` in `jinx_ai/fa.py` (green FA glyph / red slashed glyph). `ToggleSwitch` is only inside `SwitchRow` for labeled Settings rows. Do not clone a second title/subtitle/chip/button row.
- **All** icons are Font Awesome (`create_activity_icon` / `icon_button`). Activity bar, composer, explorer tree, editor tabs, git chevrons, menus, overlay HUD. No Unicode dingbats. Add a `_KIND_FA` mapping for every new kind.
- One QSS file: `jinx_ai/ui_style.py`. Overlay HUD styles stay on the overlay widget.
- Frameless main window uses traffic lights (min / max / close) on the **right**, Windows order.
- Scrollbars: 7px overlay track, no arrows, no chunky Windows chrome.
- Overlay: particle body first (orb with glyph effects, or a female figure with hair). Double-click opens a HUD with Call (green phone in-call / red phone-slash to start), Mute (during a call), and Close. No letter-`x` close. Voice is a call, not a Hey Jinx wake word. Transparent look can drop the outline via `hide_border`.
- Models: activity bar **AI Models**. Inner nav: General, Local, Hugging Face Hub, Downloads, Cloud API, ComfyUI. General = one grouped combo per role (Chat, Coding, Memory, Images). Hub uses info cards in a two-column flex grid, 20 per page (VRAM, Hub description, vision/tools, GPU fit). **Recommended** starts on. Titlebar download icon while a pull is active (or after the first pull); click it for a compact flyout (progress + folder-open icon on the current row, queue, last 5, history). Compact single-line model rows on Local. Pulls queue; Hub cards and the Local strip show a progress bar.
- Settings: left nav (`settingsNav` / `settingsNavBtn`) with General, API Access, Connectors, Skills, Appearance. Checked = faint cyan fill.
- Copy, comments, README: English.
- Landing is **Home** (Projects + Teams), then **Members**. Project Explorer docks chat. Computer is Files takeover. **New project** has a Computer folder row (FA `folder-open`), team checkboxes, and Kickoff (new only). Chat bubbles inside a project show **Chief of Staff** or the bot name — not “Jinx AI”. Tool activity in chat is a **Worked for Xm Ys** line with a FA chevron (not a status card). No Chat activity tab and no Assistant/Developer mode toggle.

## Palette

| Token | Hex |
| --- | --- |
| Void | `#090b12` |
| Surface | `#12151f` |
| Hairline | `rgba(255,255,255,0.08)` |
| Text | `#f5f5f7` |
| Mute | `#8e8e93` |
| Cyan | `#22d3ee` |
| Pink | `#f472b6` |
| Traffic close / min / zoom | `#ff5f57` / `#febc2e` / `#28c840` |

Primary actions: cyan glass. Danger hover: red glass. Selected nav: faint cyan fill, not a violet brick.

## Density

Page padding 20–24. Cards 12px radius, 10–12px inset. Model rows ~36–40px tall. Titlebar 48px. Activity bar 52px, 36px icons.
