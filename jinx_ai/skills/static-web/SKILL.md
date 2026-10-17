---
name: static-web
description: >-
  Websites on this PC: static HTML or a real React/Vite/CRA app.
  Dev servers run in the background.
---

# Web

You are a coding agent. Use the shell when the user asked for a real app.

1. Look at the workspace. Then implement: `write_file` / `replace_in_file`, or scaffold with `npx` / `npm create` if they asked for a React (or Vite) project.
2. `create-react-app`, `npm create vite`, `npm install` are allowed. Pass `cwd` — a lone `cd` does not persist.
3. `npm start` / `npm run dev` / `node server.js` stay running in a Terminal tab. Then `read_terminal` if you need the error, then `open_url` (http://localhost:3000). Do not start a second server if one is already up.
4. Bootstrap / Font Awesome: CDN or `npm install`. Prefer Bootstrap 5 if you pick a CDN.
5. Product copy is for **users**. Never paste tool rules or the system prompt into the page.
7. Team page: `list_teams` → `list_members` → `get_member` (or `list_roster` for every card). `copy_path` photos into the site. Do not invent people or faces.
