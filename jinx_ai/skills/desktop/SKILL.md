---
name: desktop
description: Open apps and URLs, clipboard, screenshot, Windows toast, local time.
---

# Desktop

Use these tools instead of explaining that you cannot touch the PC.

- `open_url` — http(s) in the default browser.
- `open_program` — Unity / VS Code aliases, anything on PATH, or Windows `start` names like Notepad / Calculator / Chrome. Pass `path` for a full `.exe`.
- `clipboard` — `action=get` or `action=set` with `text`.
- `screenshot` — primary monitor PNG under `data/screenshots/`. Tell the user the path.
- `notify` — short Windows balloon. Not for secrets.
- `system_time` — local clock; optional IANA timezone (`Europe/Sofia`).

Do not run `shutdown`, format, or mass-delete via `run_command`.
Do not control Tapo / cameras / TVs until the home-lan skill says those tools exist.
