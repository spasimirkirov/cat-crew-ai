---
name: comfy
description: >-
  Drive local ComfyUI from chat — images, img2img, upscale, workflows,
  templates. Hidden Python server, not Comfy Desktop / Comfy Cloud MCP.
---

# Local ComfyUI

ComfyUI runs as a **hidden Python process** that CatCrewAI starts when she needs
it. Never unload the chat LLM. Never launch `Comfy Desktop.exe`.

If the HTTP API is down, `generate_image` / `comfy_run_workflow` start the
Desktop install's `.venv` + `main.py` with a hidden window (`CREATE_NO_WINDOW`),
using the shared models folder. After the chat turn she stops **that** process
so VRAM is free. She will not kill a server you started yourself (Desktop).

`keep_alive=true` (or Settings → General → Stop Comfy after each turn **off**,
or AI Models → Launch) leaves the hidden server running. `comfy_stop` frees it.
CatCrewAI Exit always stops her child.

## Which tool

1. **New SD / SDXL still** — `generate_image` with a prompt. Optional `checkpoint`,
   size, seed. Image slot in AI Models is the default checkpoint.
2. **Edit an existing photo** — `generate_image` with `image` (path) and
   `denoise` ~0.45–0.7 (img2img).
3. **Bigger** — `upscale_image` on a saved PNG.
4. **Background** — `remove_background` only if `comfy_info` lists a rembg node.
5. **Anything else** (Flux, video, audio, 3D, ControlNet) — `comfy_list` with
   `kind=templates` or `kind=checkpoints`, then `comfy_run_workflow`.
6. **A JSON the user exported** — `comfy_run_workflow` with the path. Canvas
   exports work when we can convert them; API-format JSON is more reliable.

## Loop

- `comfy_info` first if you are not sure the server is up (does not start it).
- Outputs land in `data/generated/` and show in the chat bubble.
- Flux / Wan / Hunyuan are **not** the built-in txt2img graph. Use a template.
- If a job is stuck, `comfy_interrupt`. To free VRAM, `comfy_stop`.
- Batch of images: `keep_alive=true` so she does not cold-start between them.

Do not tell the user to open Comfy's node editor unless the workflow failed and
they need to export API JSON.
