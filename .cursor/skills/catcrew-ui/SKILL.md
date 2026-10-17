---
name: catcrew-ui
description: >-
  Visual language and structure of the CatCrewAI desktop UI (Tauri + React + Tailwind in frontend/).
  Use when changing pages, components, colours, execution cards, the Cat Crew chat, the WebSocket
  state, or the native window lifecycle.
---

# CatCrewAI UI

Dark, quiet, technical. Cyan, emerald and violet are sparks, not walls of colour.

## Must

- The UI is a **pure client** of the backend (`docs/API.md`). No business logic, no SQLite, no filesystem access. REST for state, `/ws/events` for live changes; a `hello` frame means "refetch over REST" (the server has no replay).
- State: `src/state/reducer.ts` is a pure `reduce(state, event)`; `store.ts` (zustand) holds actions; `ws.ts` is the reconnecting socket; `live.ts` wires them. New live behaviour = a reducer case with a test, not component-local WebSocket code.
- **Home is a launcher only** (recent projects, New project, Open folder). **Never show tasks on Home.** Tasks live in the project workspace (Fleet / Tasks / Changes sidebar tabs beside the Cat Crew chat) and in in-chat execution cards.
- **Real-time pop-in:** cats appear in the Fleet sidebar the moment `bot.staffed` arrives (`store.fresh` drives the `animate-pop-in` class). No refresh, no route change.
- **Two-line cards:** every activity / trace / diff callout uses `line-clamp-2` (`ExecutionCard`, `DiffBlock`) with a **See more** toggle for the full log. Long chat text uses `CollapsibleText` (5 lines for a cat's work report, 12 for conversation) with the same toggle. Do not add a card that dumps unbounded text inline.
- Icons are Font Awesome (`@fortawesome/react-fontawesome`), never emoji or Unicode dingbats.
- Timestamps go through `src/lib/time.ts`: "X mins ago" / "X hours ago" under 24 h, an exact date after that.
- **Window lifecycle:** the native window is declared `visible: false` with `backgroundColor #0B0D12` in `src-tauri/tauri.conf.json`; `src/lib/window.ts` shows it after the first painted frame (Rust shows it after 4 s as a failsafe). Do not create windows from the front end, and keep `index.html`'s inline `#0B0D12` background.
- **Start-up:** `BootGate` (in `App.tsx`) shows `StartupScreen` until the first `/api/bootstrap` has loaded. An unreachable backend is normal for the first seconds, so `state/boot.ts` retries quietly; only a real failure (shell `backend_status` = failed, a non-network API error, or 30 s of silence) becomes a message with Try again. Never render "Cannot reach the backend" during launch.
- **Tray / quit:** the X button hides to the system tray; real quit is the tray's "Quit CatCrewAI" or the sidebar Power Off button (`components/PowerOff.tsx` -> `quitApp()` in `lib/window.ts` -> the `quit_app` command in `src-tauri/src/lib.rs`). The Power Off button renders only inside Tauri (`isTauri()`). Never call `window.close()` or `process.exit`-style shortcuts from the UI.
- Secrets never reach the UI: the API returns `configured: true/false`, not keys.
- Copy, comments, README: English.

## Palette (Tailwind tokens in `tailwind.config.js`)

| Token | Hex |
| --- | --- |
| `bg` | `#0B0D12` |
| `card` | `#13161F` |
| `line` (1px border) | `#222634` |
| `cyan` | `#38BDF8` |
| `emerald` | `#34D399` |
| `violet` | `#A78BFA` |
| `muted` | `#8B92A8` |

Font: Inter (`@fontsource-variable/inter`, bundled, no CDN). Shared classes live in `src/index.css` (`.card`, `.btn`, `.chip`, `.input`, ...); reuse them instead of cloning button or chip markup.

## Density

Page padding 24. Cards 12px radius, 12px inset, 1px `line` border. Nav rail 68px. Fleet cards 40px avatars.

## Checks

`cd frontend && npm run typecheck && npm test && npm run build`.
