---
name: unity-mcp
description: >-
  Drive the Unity Editor through the Unity MCP server (Pulse only) — create GameObjects,
  attach scripts, read the console, build, play. Includes the C# compile-error fix loop.
---

# Unity through MCP

Only **Pulse** gets these tools. They appear as `mcp__unity__<name>`, plus `unity_compile_check`.
If the list is long, `mcp_find_tools` (server `unity`) finds one by keyword and `mcp_call_tool` runs it.

## Two doors to the Editor

Pulse can have two Unity servers, and the tool names say which: `mcp__unity__*` is MCP for Unity
(HTTP, port 8080; it also has `unity_compile_check`), `mcp__unity_relay__*` is Unity's relay
(`relay_win.exe --mcp`, started by CatCrewAI as a child process). Their tool names differ, so read the
schema of the one you call instead of assuming. Use whichever is connected; if both are, prefer the one
whose tool does the job with fewer calls, and do not send the same change through both. The compile loop
below needs `unity_compile_check`; with only the relay, edit the file and read the Editor console
through the relay's own console tool, then fix the first error the same way.

## Is Unity there?

No `mcp__unity__*` tool means the server at `http://localhost:8080/mcp` is not reachable (Unity is
closed, the helper is not installed, or the MCP for Unity package is missing). Say so once,
point to Models → MCP Integrations and `integrations/unity/CatCrewUnityMCP.cs`, and
`report_task_results` status `blocked`. Editing `.cs` files on disk still works without Unity, but
you cannot claim they compile.

## The compile-fix loop (C# in `Assets/Scripts/`)

1. Write or edit the script with the normal file tools (`replace_in_file`, `write_file`).
2. Call `unity_compile_check`. It refreshes the Editor, reads the console and returns each
   error as `file:line CSxxxx message` with the source lines around it.
3. Fix the **first** error first (later ones are often its echo) with `replace_in_file`.
   Read the surrounding lines it shows; do not rewrite the whole file.
4. Call `unity_compile_check` again. Repeat until it says "Compiles clean".
5. **At most 5 rounds.** If the same error comes back twice unchanged, or the rounds run out, stop:
   `report_task_results` status `blocked` with the file, the error code and what you tried.
6. Ignore errors outside `Assets/Scripts/` (packages, generated code) unless they block you.

Common ones: CS0246 missing `using` or type (check the namespace, assembly definition), CS1002 `;`
expected, CS0103 name does not exist, CS0117 wrong member, CS0029 type mismatch, CS0101 duplicate class
(two files declare the same class).

## Other work

- **Scene**: `manage_gameobject` (create / modify / find) and the component tools. Look up an object
  before editing it; name new objects clearly.
- **Scripts**: create the `.cs` file on disk, run the compile loop, only then attach it to a GameObject.
  The class name must equal the file name for a `MonoBehaviour`.
- **Assets**: put files under `Assets/` first, then import/refresh (`refresh_unity`). Textures and models
  from Pixel arrive under `Assets/Models/` or `Assets/Textures/`.
- **Play mode / build**: `manage_editor` (play, stop) and `manage_build`. Read the console after entering
  play mode; a script error there is a runtime error, not a compile error. Leave play mode when done.
- **Tests**: the `testing` tool group is off by default; activate it with `manage_tools` before `run_tests`.
- Approvals: changing tools may ask the user to confirm. Wait; do not retry around it.
