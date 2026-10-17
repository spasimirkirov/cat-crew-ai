---
name: static-web
description: >-
  Websites on this PC: static HTML or a real React/Vite/CRA app.
  Dev servers run in the background. Laragon www folders must be built.
---

# Web

You are a coding agent. Use the shell when the user asked for a real app.

1. Look at the workspace. Then implement: `write_file` / `replace_in_file`, or create the project with `scaffold_project` if they asked for a React (or Vite) project.
2. `npm install` and dev servers run through `run_command`. Omit cwd — the shared computer is already the working directory. A lone `cd` does not persist. Never pass `D:\...` as cwd (backslashes break).
3. If they asked for React/Vite and there is no `vite.config.js`, the **first** call is `scaffold_project` with `npm create vite@latest {dir} -- --template react` (it works when the folder already has files). Nobody scaffolds for you — the app is yours to create. Do **not** `npm install bootstrap` first (that makes a fake package.json and you will skip Vite).
4. Then `npm install` and `npm install bootstrap @fortawesome/fontawesome-free`. Set `base: './'` in `vite.config.js`.
5. Write real components. Empty `return <div></div>` is not a page. Paste roster data into source (`list_roster` in this turn). Never call CatCrewAI tools from React (`list_teams` inside App.jsx will not run in the browser).
6. `npm start` / `npm run dev` / `node server.js` stay running in a Terminal tab. Then `read_terminal` if you need the error, then `open_url` (http://localhost:5173). Do not start a second server if one is already up.
7. **Laragon (`D:\laragon\www\<name>` → http://&lt;name&gt;.local/):** Apache does not compile JSX. After the page is written, `npm run build`. `index.php` must `readfile` from `dist/` (no Location redirect): map `/` to `dist/index.html`, otherwise join the request URI onto `dist/`, check `realpath` stays inside `dist/` and is a file, set `Content-Type` by extension, then `readfile` it; 404 with a plain-text note if the build is missing. You must also `write_file` a `.htaccess` with `RewriteEngine On`, `RewriteCond %{REQUEST_FILENAME} !-f`, `RewriteRule . index.php [L]` — otherwise `/` may load the dev `index.html` and `/assets/*` 404s, so the page stays blank. Do not skip either file.
8. Product copy is for **users**. Never paste tool rules or the system prompt into the page.
9. Team page: `list_teams` → `list_members` → `get_member` (or `list_roster` for every card). `copy_path` photos into the site. Do not invent people or faces.
