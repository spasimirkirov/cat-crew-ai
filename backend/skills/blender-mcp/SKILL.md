---
name: blender-mcp
description: >-
  Drive a running Blender through the Blender MCP server (Pixel only) — inspect the scene,
  run bpy scripts, make materials, render, export FBX. Use when a task needs 3D assets.
---

# Blender through MCP

Only **Pixel** gets these tools. They appear in your tool list as `mcp__blender__<name>`; if the
list is long, `mcp_find_tools` (server `blender`) finds one by keyword and `mcp_call_tool` runs it.

## Is Blender there?

No `mcp__blender__*` tool and no `mcp_find_tools` in your list means the Blender server is
offline (Blender is closed, the add-on is not running, or `mcp-blender` is not installed). Do not
invent results and do not try to run `blender` from the Terminal instead. Say so once, point to
Models → MCP Integrations (Reconnect) and `integrations/blender/install_addon.py`, and finish with
`report_task_results` status `blocked`.

A call that answers "the program is closed" or "connection refused" means Blender went away
mid-task: stop, do not loop.

## Working order

1. **Look first**: scene info (`scene_info` / `get_scene_info`) — objects, units, active frame.
   Never assume the scene is empty or that an object name exists.
2. **Small scripts**: `execute_script` / `execute_blender_python` runs Python inside Blender. Keep each
   script to one job, `print()` what you need back, and name every object you create so the next
   step can find it. Use `bpy.data` for data, `bpy.ops` only when there is no data API.
3. **Check after every change**: read the scene info again or print the object's location,
   dimensions and material slots. A script that raised no error can still have done nothing.
4. **Materials**: create them by name (`material_create` / `create_material`), assign to a slot,
   then look at a render or screenshot before saying they are right.
5. **Render**: `render_image` / `render_frame` at a low resolution first (e.g. 512 px, few samples).
   Images come back as files; mention the path, do not paste them.
6. **Export**: FBX or glTF into the project folder (for Unity: `Assets/Models/`), with
   apply-transforms on and the scale the game expects (Unity: 1 unit = 1 m, FBX `apply_scale_options=FBX_SCALE_ALL`).
   Confirm the file exists and is not empty with the file tools.

## Rules

- Do not delete objects you did not create. Ask the lead before wiping the scene.
- Save the `.blend` (`bpy.ops.wm.save_mainfile()`) before a risky script when the file has a path.
- Approvals: tools that change the scene may ask the user to confirm. Wait; do not retry around it.
- Hand-off to Pulse (Unity): report the exported path, scale, and the object/material names.
