"""Did a tool call fail? One answer for the whole harness.

Tools return text, and every place that needed to know "did it work" used to test that text for an
``Error:`` prefix on its own. That missed most real failures -- ``run_command`` reports
``exit 1 @ <cwd>``, a timeout says ``Command timed out``, the approval gate says ``Denied by user
approval gate``, a smoke test says ``FAIL:`` -- so a failed build counted as progress, was
recorded as a project artifact, and let ``restart_catcrew`` run right after it. ``tool_failed``
reads each tool's own result format; the loop guard, the restart ordering, the artifact log and
compaction all ask it.
"""

from __future__ import annotations

import re

# Text the harness itself puts in front of a call that did not do its job.
_FAIL_PREFIXES = (
    "error:",
    "error running command",
    "refused:",
    "refused a ",
    "denied by user approval gate",
    "jev gatekeeper blocked",
    "cd does not persist",
    "command timed out",
    "stopped: command aborted",
    "code mode error",
    "subagent failed",
    "could not start a terminal session",
    "smoke test lost its terminal session",
)

# ``run_command`` (and the role tools built on it): "exit <code> @ <cwd>" on the first line.
_EXIT_LINE = re.compile(r"^exit (-?\d+) @ ", re.I)
# ``run_test_suite``: "`<cmd>` -> FAIL (…)" as its header.
_SUITE_FAIL = re.compile(r"^`[^`]*` -> FAIL\b")
# ``run_smoke_test`` / browser checks: a verdict word first.
_VERDICT_FAIL = re.compile(r"^FAIL\b")

_COMMAND_TOOLS = frozenset({"run_command", "execute_terminal_cmd", "run_test_suite"})


def exit_code(result: str) -> int | None:
    """The exit code a command tool reported, or ``None`` when the text carries none."""
    match = _EXIT_LINE.match((result or "").lstrip())
    return int(match.group(1)) if match else None


def tool_failed(name: str, result: str) -> bool:
    """True when ``result`` says the call ``name`` did not succeed."""
    text = (result or "").lstrip()
    if not text:
        return False
    low = text[:80].lower()
    if low.startswith(_FAIL_PREFIXES):
        return True
    if _VERDICT_FAIL.match(text) or _SUITE_FAIL.match(text):
        return True
    if name in _COMMAND_TOOLS or name == "":
        code = exit_code(text)
        if code is None and name == "run_test_suite":
            # The suite summary comes first; the run_command line follows its output marker.
            _head, _sep, tail = text.partition("--- output ---\n")
            code = exit_code(tail)
        if code is not None:
            return code != 0
    return False
