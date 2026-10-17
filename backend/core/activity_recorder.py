"""Step recorder behind the chat "execution card".

While a turn runs, every phase change and tool call becomes a *step*
(``{"line": "Writing file — src/App.jsx", "preview": "args: … / result: …"}``).
The recorder keeps the last 24 steps, lets a later tool preview attach itself
to the step it belongs to, and freezes into the ``{"elapsed_s", "steps"}``
snapshot that is stored on the finished assistant message and rendered by the
frontend as a compact, 2-line-clamped card with a "See more" toggle.

Ported from the desktop transcript's ``ActivityCard`` (same step semantics), with
no widget code. Not thread-safe by itself: the chat service serialises access.
"""

from __future__ import annotations

import time
from typing import Any

MAX_STEPS = 24


class ActivityRecorder:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self._n = 0
        self._started = time.monotonic()

    def _new_step(self, line: str, preview: str = "") -> dict[str, Any]:
        self._n += 1
        step = {"n": self._n, "line": line, "preview": preview}
        self.steps.append(step)
        del self.steps[:-MAX_STEPS]
        return step

    def add_step(self, line: str, preview: str = "") -> dict[str, Any]:
        """A step with its own wording (a question the person answered, say)."""
        return self._new_step(line, preview)

    def set_status(self, title: str, detail: str = "") -> dict[str, Any] | None:
        """A phase/tool step. Returns the new step, or ``None`` if it repeats the last one."""
        title = title or "Working"
        detail = (detail or "").strip()
        line = f"{title} — {detail}" if detail else title
        if self.steps and self.steps[-1]["line"] == line:
            return None
        return self._new_step(line)

    def set_tool_preview(
        self, name: str, args_preview: str = "", result_preview: str = ""
    ) -> dict[str, Any] | None:
        """Attach ``args``/``result`` to the newest step that mentions the tool."""
        blob = ""
        if args_preview:
            blob += f"args: {args_preview.strip()[:400]}"
        if result_preview:
            if blob:
                blob += "\n"
            blob += f"result: {result_preview.strip()[:600]}"
        if not blob:
            return None
        needle = (name or "").replace("_", " ").lower()
        for item in reversed(self.steps):
            if needle and needle in item["line"].lower():
                item["preview"] = blob
                return item
        return self._new_step(name or "tool", blob)

    def elapsed(self) -> float:
        return max(0.0, time.monotonic() - self._started)


    def snapshot(self, elapsed_s: float | None = None) -> dict[str, Any]:
        """Frozen form stored on the message (``{}`` when nothing ran)."""
        if not self.steps:
            return {}
        return {
            "elapsed_s": round(self.elapsed() if elapsed_s is None else float(elapsed_s), 1),
            "steps": [{"line": s["line"], "preview": s["preview"]} for s in self.steps],
        }
