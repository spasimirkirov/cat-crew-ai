"""State-aware repeat detection for the agent tool loop.

``BaseProvider.run_agent`` is the one place that stops a cat from re-issuing the same tool call
(``platform/runner`` used to add a second throttle; it is gone). The old guards were blind to
progress, so ``npm run build`` -> fix a file -> ``npm run build`` was treated as a loop and the
cat ended the turn with "I got stuck repeating the same failed tool".

A repeat is only a repeat when *nothing changed in between*. ``WorldClock`` counts the tool
calls that can change the project or its environment; a remembered call is reusable only
while the clock still shows the value it had when the call ran.
"""

from __future__ import annotations

from backend.harness import tool_meta

# Tools that only look at things, and the harness's own bookkeeping (a cat spamming update_todo
# with the same list is exactly the loop to catch): neither changes the world. Everything else --
# including any tool not in harness/tool_meta.py -- is assumed to, which is the safe default: it
# lets a verifying call run again.
READ_ONLY_TOOLS = tool_meta.names(tool_meta.READ)
NEUTRAL_TOOLS = READ_ONLY_TOOLS | tool_meta.names(tool_meta.NOTE)
# Read tools whose answer changes on its own (a dev server's terminal, a live page): repeating one
# is waiting for news, not a loop -- up to VOLATILE_REPEATS times while nothing else changes.
VOLATILE_TOOLS = tool_meta.flagged("volatile")
VOLATILE_REPEATS = 3


def changes_world(tool_name: str) -> bool:
    return tool_name not in NEUTRAL_TOOLS


class WorldClock:
    """A counter that ticks whenever an executed tool may have changed the project."""

    def __init__(self) -> None:
        self.version = 0

    def note_executed(self, tool_name: str) -> int:
        if changes_world(tool_name):
            self.version += 1
        return self.version
