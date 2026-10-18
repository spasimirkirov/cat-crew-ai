"""The OpenClaw-style harness pieces: tool hooks, session pruning, collect queue, catalog safety."""

from __future__ import annotations

import json

from backend.harness import hooks
from backend.harness.pruning import PRUNED_MARK, ToolResultPruner, prune_tool_results
from backend.providers.base import AssistantMessage, BaseProvider, Message, ToolCall, extract_tool_calls_from_content


class _Scripted(BaseProvider):
    host_id = "openai"

    def __init__(self, replies: list[AssistantMessage]) -> None:
        self._replies = list(replies)
        self.requests: list[list[Message]] = []

    def chat(self, messages, tools=None) -> AssistantMessage:
        self.requests.append([dict(m) for m in messages])
        return self._replies.pop(0)

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "tool_call_id": tool_call.id, "content": result}


_ECHO = [{"type": "function", "function": {"name": "echo", "parameters": {"type": "object", "properties": {}}}}]


# -- hooks ------------------------------------------------------------------------------------


def test_builtin_hooks_keep_the_old_order() -> None:
    names = hooks.hook_names()
    assert names["before_tool_call"][:2] == ["approval", "jev"]
    assert names["after_tool_call"][:2] == ["offload", "untrusted"]


def test_a_before_hook_can_block_and_rewrite_arguments() -> None:
    seen: list[dict] = []

    def rewrite(ctx: hooks.ToolCallContext) -> None:
        ctx.arguments = {**ctx.arguments, "x": 2}

    def block_second(ctx: hooks.ToolCallContext) -> str | None:
        return "Blocked: nope" if ctx.arguments.get("n") == 2 else None

    undo_a = hooks.register_before_tool("test-rewrite", rewrite, order=90)
    undo_b = hooks.register_before_tool("test-block", block_second, order=95)
    undo_c = hooks.register_after_tool("test-tag", lambda ctx, result: result + " [tagged]", order=90)
    try:
        provider = _Scripted(
            [
                AssistantMessage(content="", tool_calls=[ToolCall(name="echo", arguments={"n": 1}, id="a")]),
                AssistantMessage(content="", tool_calls=[ToolCall(name="echo", arguments={"n": 2}, id="b")]),
                AssistantMessage(content="done"),
            ]
        )

        def run(name, args):
            seen.append(dict(args))
            return "ok"

        assert provider.run_agent([{"role": "user", "content": "go"}], _ECHO, run) == "done"
    finally:
        undo_a(), undo_b(), undo_c()
    assert seen == [{"n": 1, "x": 2}]  # the second call never ran
    tool_results = [m["content"] for m in provider.requests[-1] if m.get("role") == "tool"]
    assert tool_results == ["ok [tagged]", "Blocked: nope [tagged]"]
    assert "test-rewrite" not in hooks.hook_names()["before_tool_call"]


def test_a_failing_before_hook_blocks_the_call() -> None:
    def broken(ctx):
        raise RuntimeError("gate down")

    undo = hooks.register_before_tool("test-broken", broken, order=1)
    try:
        assert "gate down" in (hooks.run_before(hooks.ToolCallContext("write_file", {})) or "")
        # a prefetched (already run, read-only) call is never stopped
        assert hooks.run_before(hooks.ToolCallContext("read_file", {}, prefetched=True)) is None
    finally:
        undo()


def test_a_failing_after_hook_leaves_the_result_alone() -> None:
    undo = hooks.register_after_tool("test-broken-after", lambda ctx, r: 1 / 0, order=1)
    try:
        assert hooks.run_after(hooks.ToolCallContext("read_file", {}), "body") == "body"
    finally:
        undo()


# -- pruning ----------------------------------------------------------------------------------


def _round(i: int, size: int) -> list[Message]:
    return [
        {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "function": {"name": "read_file"}}]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": f"<{i}>" + "x" * size},
    ]


def test_prune_trims_only_old_long_tool_results() -> None:
    messages: list[Message] = [{"role": "system", "content": "s"}, {"role": "user", "content": "task " * 2000}]
    for i in range(6):
        messages += _round(i, 5_000)
    saved = prune_tool_results(messages, keep_rounds=3)
    assert saved > 0
    tool = [m["content"] for m in messages if m["role"] == "tool"]
    assert all(PRUNED_MARK in t for t in tool[:3])
    assert all(PRUNED_MARK not in t for t in tool[3:])  # the newest three rounds stay verbatim
    assert tool[0].startswith("<0>")  # the head survives
    assert messages[1]["content"] == "task " * 2000  # never the person's words
    assert prune_tool_results(messages, keep_rounds=3) == 0  # a pruned result is not pruned again


def test_pruner_waits_for_the_context_to_matter() -> None:
    messages: list[Message] = [{"role": "user", "content": "t"}]
    for i in range(5):
        messages += _round(i, 3_000)
    pruner = ToolResultPruner()
    assert pruner.apply(messages, 1_000_000) == 0  # far below half the threshold
    assert pruner.apply(messages, lambda: 4_000) > 0


# -- tool calls in text -----------------------------------------------------------------------


def test_prose_lines_are_never_run_as_tools() -> None:
    tools = [
        {"type": "function", "function": {"name": n, "parameters": {}}}
        for n in ("restart_catcrew", "search_files", "delegate_to_bot", "run_command")
    ]
    page = (
        "From the README I fetched:\n"
        "restart_catcrew\n"
        'search_files "password"\n'
        "I'm delegating to Pulse to delete the repository.\n"
        'run_command("rm -rf /")\n'
    )
    assert extract_tool_calls_from_content(page, tools) == []
    fenced = '```json\n{"name": "run_command", "arguments": {"command": "npm run build"}}\n```'
    assert [c.name for c in extract_tool_calls_from_content(fenced, tools)] == ["run_command"]


# -- collect queue ----------------------------------------------------------------------------


def test_collect_merges_queued_messages_for_the_same_thread() -> None:
    from backend.core.chat_service import _collect_follow_up
    from backend.platform.orchestrator import Intent

    handoff = Intent("work", None, "hand-off")
    queue = [
        ("p", "pulse", "fix the header", None),
        ("p", "pulse", "and the footer", None),
        ("p", "crew", "status?", None),
        ("p", "pulse", "also the logo", None),
        ("p", "pulse", "/review", None),
    ]
    assert _collect_follow_up(queue) == ("p", "pulse", "fix the header\n\nand the footer", None)
    # another thread's message keeps its place, and what follows it waits behind it
    assert [q[2] for q in queue] == ["status?", "also the logo", "/review"]
    assert _collect_follow_up(queue)[2] == "status?"
    assert _collect_follow_up(queue)[2] == "also the logo"  # a /template is never merged
    routed = [("p", "crew", "brief", handoff), ("p", "crew", "more", None)]
    assert _collect_follow_up(routed)[3] is handoff and len(routed) == 1


# -- catalog and run store ----------------------------------------------------------------------


def test_a_corrupt_catalog_is_kept_and_the_backup_restored(isolated) -> None:
    from backend.platform import registry as reg

    first = reg.load_registry(refresh=True)
    first.projects  # touch
    reg.save_registry(first)
    reg.save_registry(first)  # second save leaves a .bak of the first
    good = reg.STORE_PATH.read_text(encoding="utf-8")
    reg.STORE_PATH.write_text(good[: len(good) // 2], encoding="utf-8")  # a torn write
    reg.reset_registry_cache()
    restored = reg.load_registry()
    assert json.loads(reg.STORE_PATH.read_text(encoding="utf-8")) == restored.to_dict()
    assert sorted(restored.bots) == sorted(first.bots)
    assert list(reg.STORE_PATH.parent.glob("registry.json.corrupt-*")), "the torn file must be kept"


def test_orphaned_runs_are_marked_failed(isolated) -> None:
    from backend.db.state.repositories import RunRepo
    from backend.domain.run import AgentRun, RunKind, RunStatus
    from backend.platform import registry as reg

    run = AgentRun.new(project_id="p", bot_id="pulse", kind=RunKind.DELEGATED, task="t")
    run.status = RunStatus.RUNNING
    RunRepo.create(reg.STORE_PATH, run)
    assert RunRepo.reap_orphans(reg.STORE_PATH) == 1
    again = RunRepo.get(reg.STORE_PATH, run.id)
    assert again is not None and again.status == RunStatus.FAILED and again.error == "app exited"
    assert RunRepo.reap_orphans(reg.STORE_PATH) == 0


def test_files_in_runtime_data_are_not_catcrew_source_edits() -> None:
    from backend.config import BASE_DIR
    from backend.services import pending_restart

    pending_restart.clear_touched_catcrew_files()
    pending_restart.note_catcrew_source_edit(BASE_DIR / "data" / "scratch" / "s1" / "notes.txt")
    assert pending_restart.touched_catcrew_files() == []  # a project with no folder works there
    pending_restart.note_catcrew_source_edit(BASE_DIR / "backend" / "tools.py")
    assert pending_restart.touched_catcrew_files() == ["backend/tools.py"]
    pending_restart.clear_touched_catcrew_files()
