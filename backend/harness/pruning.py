"""Session pruning: trim stale tool results before the context needs an LLM summary.

Most of a long coding run's context is tool output the model has already acted on: a file it read
ten rounds ago, a build log from before the fix. Compaction (``services/compaction``) summarises the
older turns with a model call, which costs a request and loses detail. Pruning (OpenClaw's "session
pruning") is the cheap step in front of it: once the context passes half of the compaction
threshold, every tool result older than the last ``KEEP_ROUNDS`` tool rounds that is longer than
``SOFT_LIMIT`` characters is cut to its head and tail with a note that says how to get it back.

Two rules keep it friendly to prompt caching (``harness/turn_note.py``):

* it changes messages in place and a pruned message is never pruned again, so the prefix moves
  only when a prune happens, not on every round;
* after a prune it waits until the context has grown by a quarter before looking again.

The assignment, the person's words and the model's own messages are never touched; only
``role == "tool"`` messages are.
"""

from __future__ import annotations

from typing import Any, Callable

KEEP_ROUNDS = 3  # the newest tool rounds keep their results verbatim
SOFT_LIMIT = 2_000  # characters; shorter results are left alone
HEAD = 700
TAIL = 500
START_RATIO = 0.5  # of the compaction threshold
REGROW = 1.25
PRUNED_MARK = "[pruned by the harness:"


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    from backend.services.compaction import estimate_tokens

    return estimate_tokens(messages)


def _stale_tool_indexes(messages: list[dict[str, Any]], keep_rounds: int) -> list[int]:
    """Indexes of tool messages that belong to rounds older than the newest ``keep_rounds``."""
    rounds_seen = 0
    cutoff = len(messages)
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") == "assistant" and message.get("tool_calls"):
            rounds_seen += 1
            if rounds_seen == keep_rounds:
                cutoff = index
                break
    else:
        return []  # fewer tool rounds than we keep: nothing is stale
    return [i for i in range(cutoff) if messages[i].get("role") == "tool"]


def trim_text(text: str, *, head: int = HEAD, tail: int = TAIL) -> str:
    cut = len(text) - head - tail
    return (
        f"{text[:head].rstrip()}\n"
        f"{PRUNED_MARK} {cut} characters of an old tool result removed to save context; "
        "run the tool again (or read_file the path) if you need them]\n"
        f"{text[-tail:].lstrip()}"
    )


def prune_tool_results(
    messages: list[dict[str, Any]],
    *,
    keep_rounds: int = KEEP_ROUNDS,
    soft_limit: int = SOFT_LIMIT,
) -> int:
    """Trim stale, long tool results in place. Returns the number of characters removed."""
    saved = 0
    for index in _stale_tool_indexes(messages, max(1, keep_rounds)):
        message = messages[index]
        content = message.get("content")
        if not isinstance(content, str) or len(content) <= max(soft_limit, HEAD + TAIL + 200):
            continue
        if PRUNED_MARK in content:
            continue
        trimmed = trim_text(content)
        saved += len(content) - len(trimmed)
        messages[index] = {**message, "content": trimmed}
    return saved


class ToolResultPruner:
    """One per conversation loop; decides when pruning is worth moving the cached prefix."""

    def __init__(self, *, start_ratio: float = START_RATIO) -> None:
        self.start_ratio = start_ratio
        self._retry_above = 0
        self.saved_chars = 0

    def apply(self, messages: list[dict[str, Any]], threshold: "int | Callable[[], int]") -> int:
        """Prune when the context is past ``start_ratio`` of ``threshold`` (the compaction
        threshold in tokens, or a function returning it)."""
        try:
            threshold_tokens = int(threshold() if callable(threshold) else threshold)
            if threshold_tokens <= 0:
                return 0
            before = _estimate_tokens(messages)
            if before < threshold_tokens * self.start_ratio or before <= self._retry_above:
                return 0
            saved = prune_tool_results(messages)
            self._retry_above = int(_estimate_tokens(messages) * REGROW)
            self.saved_chars += saved
            return saved
        except Exception:  # noqa: BLE001 - an optimisation must never fail a turn
            return 0
