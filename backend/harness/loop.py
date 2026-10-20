"""The agent turn loop: call the model, run the tools it asks for, repeat until it answers.

Every turn in CatCrewAI runs here: a 1:1 chat, Cat Crew's chat, each SOP specialist and each
subagent. It used to be a 380-line method on ``BaseProvider``; providers now only speak HTTP
(``chat`` / ``stream_chat`` / ``tool_message``) and ``BaseProvider.run_agent`` forwards here.

What one round does, in order: provider swap (Steer may change the model), session pruning
(``harness/pruning``), the context budget (``services/compaction``), the model call, tool calls
(native, or explicit syntax printed as text), the repeat guard (``harness/loop_guard``), the tool
hooks (``harness/hooks``: approval, Jev, offload, untrusted wrapper), then one user message with
every harness nudge of the round and the person's steer last.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

from backend.harness import tool_meta as _tool_meta
from backend.harness.checkpoints import open_changes as checkpoints_open_changes
from backend.harness.model_block import ModelUnavailable
from backend.providers.base import (
    AbortFn,
    AgentAborted,
    AssistantMessage,
    BaseProvider,
    Message,
    OnText,
    OnTools,
    SteerFn,
    SwapProvider,
    ToolCall,
    ToolExecutor,
    _FENCE_BLOCK,
    _dedupe_calls,
    _slim_args_for_history,
    extract_tool_calls_from_content,
)


def run_agent(
    provider: BaseProvider,
    messages: list[Message],
    tools: list[dict[str, Any]],
    execute_tool: ToolExecutor,
    max_rounds: int = 0,
    on_text: OnText | None = None,
    abort: AbortFn | None = None,
    take_steer: SteerFn | None = None,
    on_tools: OnTools | None = None,
    swap_provider: SwapProvider | None = None,
    on_compressed: Callable[[int, int, str], None] | None = None,
    stall_rounds: int = 0,
) -> str:
    """One agent turn (see :func:`_run_turn`), inside a checkpoint so it can be undone
    (``harness/checkpoints.py``). A subagent runs inside its parent's checkpoint."""
    from backend.harness import checkpoints
    from backend.middleware.jev_gatekeeper import turn_goal

    token = checkpoints.begin(turn_goal(messages))
    try:
        return _run_turn(
            provider, messages, tools, execute_tool, max_rounds, on_text, abort, take_steer,
            on_tools, swap_provider, on_compressed, stall_rounds, verify=token is not None,
        )
    finally:
        checkpoints.end(token)


# A turn whose project check fails goes back to the agent at most this many times.
_CHECK_RETURNS = 2


def _run_turn(
    provider: BaseProvider,
    messages: list[Message],
    tools: list[dict[str, Any]],
    execute_tool: ToolExecutor,
    max_rounds: int = 0,
    on_text: OnText | None = None,
    abort: AbortFn | None = None,
    take_steer: SteerFn | None = None,
    on_tools: OnTools | None = None,
    swap_provider: SwapProvider | None = None,
    on_compressed: Callable[[int, int, str], None] | None = None,
    stall_rounds: int = 0,
    verify: bool = False,
) -> str:
    """Full conversation loop with function calling.

    Call the model, run requested tools, return the final text.
    ``max_rounds <= 0`` means unlimited (Stop / abort ends the turn).

    ``stall_rounds > 0`` ends a run that stopped making progress: that many tool rounds in a row
    in which nothing changed (no file written, no command or action succeeded). Halfway there
    the model is told so. A cat that keeps writing files is never cut short by it; one that
    reads and re-reads in circles is (see ``hit_stall``).

    Before every model call the context is checked against the model's own window
    (``services/compaction.ContextBudget``, limits from ``services/provider_limits``): at 85 % of
    it, the older turns are summarized
    by the Memory model and ``messages`` is shortened in place, so an expensive model
    never receives a long transcript. ``on_compressed(before, after, model)`` reports it.
    """
    from backend.harness.loop_guard import VOLATILE_REPEATS, VOLATILE_TOOLS, WorldClock
    from backend.harness.pruning import ToolResultPruner
    from backend.middleware import jev_gatekeeper
    from backend.services.compaction import ContextBudget

    budget = ContextBudget(on_compressed=on_compressed)
    # Cheap first: trim stale tool results (harness/pruning.py) before the budget would pay a
    # model call to summarise the older turns.
    pruner = ToolResultPruner()
    # What Jev judges each tool call against: the request as it stood before the loop added
    # its own nudges (see jev_gatekeeper.turn_goal).
    goal = jev_gatekeeper.turn_goal(messages)
    abort = abort or (lambda: False)
    current: BaseProvider = provider
    reply = AssistantMessage(content="")
    last_text = ""
    seen_calls: dict[tuple[str, str], str] = {}
    seen_at: dict[tuple[str, str], int] = {}
    world = WorldClock()
    volatile_runs: dict[tuple[tuple[str, str], int], int] = {}
    idle_rounds = 0
    free_rounds = 0  # rounds that only touched the checklist / task report: not charged to the budget

    def _repeat(call: ToolCall) -> bool:
        """An identical call with nothing changed since -- unless the tool's answer moves on
        its own (read_terminal on a dev server), which may be asked again a few times."""
        key = _call_key(call)
        if key not in seen_calls or seen_at.get(key) != world.version:
            return False
        if call.name in VOLATILE_TOOLS:
            return volatile_runs.get((key, world.version), 1) >= VOLATILE_REPEATS
        return True
    last_skipped: tuple[str, str] | None = None
    skip_streak = 0
    empty_nudged = False
    # The end-of-turn project check (harness/project_check.verify_turn): files already checked, and
    # how many times a failed check sent the agent back.
    checked_files = 0
    check_returns = 0
    explore_n = 0
    explore_steered = False
    cut_offs = 0
    failed_over = False
    sticky = _sticky_fallback(current)
    if sticky is not None:
        current, failed_over = sticky, True
    try:
        # Jev ("System One"): once per turn, before the coding model gets its tool schemas. A
        # no-op unless the gatekeeper is on and confident no tools are needed (see
        # middleware/jev_gatekeeper.py); Jev never drafts the reply itself, so a bypass still
        # calls the model -- just without ``tools`` attached. A wrong verdict (empty reply) falls
        # straight through to the normal, tool-enabled loop below.
        bypass = jev_gatekeeper.precheck_intent(messages, tools=tools)
        if bypass is not None:
            from backend.services.usage_meter import report_context

            lean_compressed = budget.apply(messages, current)
            report_context(current, messages, budget.max_tokens, compressed=lean_compressed, threshold=budget.limit)
            lean_reply = current.stream_chat(messages, [], on_text=on_text, abort=abort)
            lean_text = (lean_reply.content or "").strip()
            if lean_text:
                return _with_auto_restart(lean_text)

        cap = int(max_rounds or 0)
        if cap < 0:
            cap = 0
        round_i = 0
        while True:
            if cap and round_i >= cap:
                break
            round_i += 1
            if abort():
                raise AgentAborted("stopped")
            if swap_provider is not None:
                nxt = swap_provider()
                if nxt is not None:
                    current.close_stream()
                    current = nxt

            held: list[str] = []

            def _on_delta(piece: str) -> None:
                held.append(piece)

            def _flush_held() -> None:
                if on_text and held:
                    on_text("".join(held))

            from backend.harness.deferred import extra_tool_schemas

            round_tools = list(tools) + extra_tool_schemas()
            pruner.apply(messages, lambda: budget.resolve(current)[1])
            compressed = budget.apply(messages, current)
            from backend.services.usage_meter import report_context

            report_context(
                current, messages, budget.max_tokens, compressed=compressed, threshold=budget.limit
            )
            asked_at = time.monotonic()
            try:
                reply = current.stream_chat(
                    messages, round_tools, on_text=_on_delta, abort=abort
                )
            except ModelUnavailable as exc:
                # The model was turned away (rate limit, no credit). With a Fallback model set on
                # the Models page, switch to it once and ask again; this round is not charged.
                nxt = None if failed_over else fallback_provider(exc)
                if nxt is None:
                    raise
                failed_over = True
                current.close_stream()
                current = nxt
                round_i -= 1
                continue
            from backend.harness.events import emit_model_call

            emit_model_call(current, round_i=round_i, seconds=time.monotonic() - asked_at, tools=len(round_tools))
            last_text = (reply.content or "").strip()
            if abort():
                _flush_held()
                return _with_auto_restart(last_text or "[stopped]")

            if reply.tool_calls and _cut_off(current):
                # The host stopped at its output limit in the middle of the tool calls: the last
                # call's arguments are incomplete (a half-written file body parses as ``{}`` or
                # as a truncated string). Running them would write broken files; ask for smaller
                # steps instead.
                cut_offs += 1
                if cut_offs > _MAX_CUT_OFFS:
                    return _with_auto_restart(_cut_off_text(current))
                print("[agent] reply hit the output limit inside a tool call; asking again", flush=True)
                if last_text:
                    messages.append({"role": "assistant", "content": last_text})
                messages.append({"role": "user", "content": _CUT_OFF_STEER})
                continue

            from_text = False
            if not reply.tool_calls:
                reply.tool_calls = extract_tool_calls_from_content(reply.content, round_tools)
                if reply.tool_calls:
                    from_text = True
                    print(
                        f"[agent] tool call from text: "
                        f"{[call.name for call in reply.tool_calls]}",
                        flush=True,
                    )
                elif not last_text and not empty_nudged:
                    # A model that says nothing and calls nothing ends the turn silently and
                    # every cat "sits idle". Ask once for an actual answer.
                    empty_nudged = True
                    print(f"[agent] empty reply ({_empty_reason(current)}); asking again", flush=True)
                    messages.append({"role": "user", "content": _EMPTY_NUDGE})
                    continue
                elif not last_text:
                    _flush_held()
                    return _with_auto_restart(_empty_text(current))
                else:
                    _flush_held()
                    workspace, changed = checkpoints_open_changes() if verify else (None, [])
                    if workspace is not None and len(changed) > checked_files and check_returns < _CHECK_RETURNS:
                        checked_files = len(changed)
                        from backend.harness.project_check import verify_turn

                        problem = verify_turn(workspace, changed)
                        if problem:
                            check_returns += 1
                            print(f"[agent] project check failed after the turn; back to the agent ({check_returns})", flush=True)
                            messages.append({"role": "assistant", "content": last_text})
                            messages.append({"role": "user", "content": problem})
                            continue
                    return _with_auto_restart(last_text)

            for index, call in enumerate(reply.tool_calls):
                if not call.id:
                    call.id = f"call_{round_i}_{index}"

            # Native OpenAI tool_calls: keep role=tool protocol.
            # Printed tool_code (Gemma/Qwen): DO NOT inject role=tool —
            # llama.cpp Jinja for those models expects user/assistant
            # alternation and returns HTTP 400 on synthetic tool turns.
            # Dedupe *before* the assistant turn is recorded: every tool_call id in the history
            # must get exactly one tool result, or the next request is rejected (HTTP 400).
            reply.tool_calls = _dedupe_calls(reply.tool_calls)
            if not from_text:
                messages.append(_assistant_turn_for(current, reply))
            last_text = ""
            fresh = [call for call in reply.tool_calls if not _repeat(call)]
            if on_tools and fresh:
                on_tools([call.name for call in fresh])

            ran_new = False
            progressed = False
            text_pairs: list[tuple[ToolCall, str]] = []
            batch_failed = False
            # Never restart in the same breath as unverified edits: run
            # restart_catcrew last, and skip it if earlier tools in this batch failed.
            ordered_calls = [
                call for call in reply.tool_calls if call.name != "restart_catcrew"
            ] + [
                call for call in reply.tool_calls if call.name == "restart_catcrew"
            ]
            # Consecutive read-only calls that share no session (files, git, web) run at the
            # same time; their results still go into the history in the model's order.
            prefetched: dict[int, Any] = {}
            for index, call in enumerate(ordered_calls):
                if abort():
                    raise AgentAborted("stopped")
                if index not in prefetched and _parallel_ok(call, _repeat):
                    run = [index]
                    while run[-1] + 1 < len(ordered_calls) and _parallel_ok(ordered_calls[run[-1] + 1], _repeat):
                        run.append(run[-1] + 1)
                    if len(run) > 1:
                        prefetched.update(_start_parallel(ordered_calls, run, execute_tool))
                key = _call_key(call)
                previous = seen_calls.get(key)
                if previous is not None and _repeat(call):
                    result = _repeat_skip_text(call, previous)
                    last_skipped = (call.name, previous)
                    from backend.harness.events import emit_tool_result, tool_info_for_name

                    emit_tool_result(
                        call.name,
                        result,
                        skipped=True,
                        arguments=call.arguments,
                        tool_info=tool_info_for_name(call.name),
                    )
                elif call.name == "restart_catcrew" and batch_failed:
                    result = (
                        "Skipped restart_catcrew: earlier tools in this batch failed. "
                        "Read the errors, fix the files, then call restart_catcrew alone."
                    )
                    from backend.harness.events import emit_tool_result, tool_info_for_name

                    emit_tool_result(
                        call.name,
                        result,
                        skipped=True,
                        arguments=call.arguments,
                        tool_info=tool_info_for_name(call.name),
                    )
                    seen_calls[key] = result
                    seen_at[key] = world.version
                else:
                    from backend.harness.events import (
                        emit_tool_call,
                        emit_tool_result,
                        tool_info_for_name,
                    )

                    info = tool_info_for_name(call.name)
                    started = emit_tool_call(
                        call.name,
                        call.arguments,
                        tool_info=info,
                        call_id=str(call.id or ""),
                    )
                    from backend.harness import hooks

                    # The lifecycle hooks (harness/hooks.py): approval gate and Jev before the
                    # call, large-result offload and the untrusted wrapper after it. A
                    # prefetched call is read-only and already ran: no before-hook stops one.
                    ctx = hooks.ToolCallContext(
                        call.name,
                        call.arguments,
                        messages=messages,
                        goal=goal,
                        prefetched=index in prefetched,
                        call_id=str(call.id or ""),
                    )
                    denied = hooks.run_before(ctx)
                    if denied:
                        result = denied
                    elif index in prefetched:
                        result = _parallel_result(prefetched.pop(index))
                    else:
                        result = execute_tool(call.name, ctx.arguments)
                    result = hooks.run_after(ctx, result)
                    if call.name in VOLATILE_TOOLS and previous is not None and seen_at.get(key) == world.version:
                        volatile_runs[(key, world.version)] = volatile_runs.get((key, world.version), 1) + 1
                    seen_calls[key] = result
                    seen_at[key] = world.note_executed(call.name)
                    ran_new = True
                    emit_tool_result(
                        call.name,
                        result,
                        call_id=str(started.payload.get("call_id") or ""),
                        arguments=call.arguments,
                        tool_info=info,
                    )
                if _failed_tool_result(result, call.name) and call.name != "restart_catcrew":
                    batch_failed = True
                elif _tool_meta.meta(call.name).kind in (_tool_meta.ACT, _tool_meta.CONTAINED) and not result.startswith("Skipped"):
                    progressed = True
                if from_text:
                    text_pairs.append((call, result))
                else:
                    messages.append(current.tool_message(call, result))

            # Steering is read once the whole batch has its results: a user message between
            # two tool results of the same assistant turn breaks the tool protocol (HTTP 400).
            # Every harness note of this round goes out as ONE user message, after the results,
            # so templates that insist on strict user/assistant alternation stay happy.
            nudges: list[str] = []

            if from_text and text_pairs:
                prose = _strip_printed_tools(reply.content) or "Working."
                messages.append({"role": "assistant", "content": prose})
                nudges.append(_text_tool_results_user(text_pairs))
                steer = _replace_fail_steer_from_pairs(text_pairs)
                if steer:
                    nudges.append(steer)
            elif batch_failed and any(
                call.name == "replace_in_file" for call in ordered_calls
            ):
                # Prefer the real tool error; only nudge when it named wrong paths.
                for call in ordered_calls:
                    if call.name != "replace_in_file":
                        continue
                    prev = seen_calls.get(_call_key(call), "")
                    steer = _replace_fail_steer(prev)
                    if steer:
                        nudges.append(steer)
                        break

            explore_n += sum(
                1 for call in ordered_calls if call.name in _EXPLORE_TOOLS
            )
            if not explore_steered and explore_n >= 6:
                explore_steered = True
                # Only meant for a fresh project: searching an empty folder over and over is a
                # loop, while reading an existing codebase before changing it is the job.
                if _workspace_is_empty():
                    nudges.append(_EXPLORE_STEER)

            if reply.tool_calls and not ran_new:
                skipped_names = {call.name for call in reply.tool_calls}
                if skipped_names & _DELEGATE_TOOLS:
                    nudges.append(_REDELEGATE_STEER)
                    skip_streak = 0
                else:
                    skip_streak += 1
                    if skip_streak >= _MAX_SKIP_STREAK:
                        return _with_auto_restart(_stuck_text(last_skipped))
                    nudges.append(_REPEAT_STEER)
            else:
                skip_streak = 0

            if (
                cap
                and free_rounds < _FREE_NOTE_ROUNDS
                and ordered_calls
                and all(_tool_meta.meta(call.name).kind == _tool_meta.NOTE for call in ordered_calls)
            ):
                # Ticking the checklist is not work on the task: it must not eat the budget the
                # work needs. Bounded, and the stall guard still sees these rounds as idle.
                free_rounds += 1
                round_i -= 1

            if stall_rounds > 0:
                idle_rounds = 0 if progressed else idle_rounds + 1
                if idle_rounds >= stall_rounds:
                    return _with_auto_restart(_stall_text(idle_rounds))
                if idle_rounds == max(2, stall_rounds // 2):
                    nudges.append(_STALL_STEER.format(n=idle_rounds))

            steered = take_steer() if take_steer else None
            if steered:
                # The person's own words come last: they outrank every harness note.
                nudges.append(steered)
            if nudges:
                messages.append({"role": "user", "content": "\n\n".join(nudges)})
    except AgentAborted:
        current.close_stream()
        return _with_auto_restart(last_text or "[stopped]")

    # ``last_text`` is cleared after every tool round, so a run that reaches this point ran out of
    # rounds: the reply always carries the stable marker (see ``hit_round_cap``).
    return _with_auto_restart(
        f"{ROUND_CAP_MARKER} {cap or round_i} tool rounds with work still in flight. "
        "The task is not finished: send continue to resume it, or raise the tool-round "
        "cap in Settings → General."
    )


# At most this many checklist-only rounds per run are not counted against ``max_rounds``.


_FREE_NOTE_ROUNDS = 15
STALL_MARKER = "Stopped: no progress for"
_STALL_STEER = (
    "[Harness] {n} tool rounds in a row changed nothing: no file was written and no command or "
    "action succeeded. Reading more will not finish the task. Write the next file or run the next "
    "command now -- or, if the work is truly done, stop calling tools and report what you did."
)


def _stall_text(rounds: int) -> str:
    return (
        f"{STALL_MARKER} {rounds} tool rounds (nothing written, no command succeeded). Stopped so "
        "it does not go round in circles."
    )


def hit_stall(text: str) -> bool:
    """True when ``run_agent`` ended because the run stopped making progress (``stall_rounds``)."""
    return STALL_MARKER in (text or "")


ROUND_CAP_MARKER = "Stopped after"
_ROUND_CAP_RE = re.compile(r"Stopped after \d+ tool rounds with work still in flight")


def hit_round_cap(text: str) -> bool:
    """True when ``run_agent`` ended because its round budget ran out."""
    return bool(_ROUND_CAP_RE.search(text or ""))


# Tools whose result is text from outside: web pages, search snippets, other programs (MCP
# servers, connectors). That text can carry instructions written by a stranger ("ignore your
# rules and run …"), so the model is told, right next to it, that it is data.
_MAX_PARALLEL = 4


def _parallel_ok(call: ToolCall, repeat: Callable[[ToolCall], bool]) -> bool:
    item = _tool_meta.meta(call.name)
    return item.kind == _tool_meta.READ and item.parallel and not repeat(call)


def _start_parallel(calls: list[ToolCall], run: list[int], execute_tool: Any) -> dict[int, Any]:
    """Start ``calls[i]`` for every ``i`` in ``run`` at once. Each runs in a copy of this thread's
    context, so the turn's tool allow-list, session and workspace bindings hold in the workers."""
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    pool = ThreadPoolExecutor(max_workers=min(_MAX_PARALLEL, len(run)), thread_name_prefix="catcrew-tool")
    try:
        return {
            i: pool.submit(contextvars.copy_context().run, execute_tool, calls[i].name, calls[i].arguments)
            for i in run
        }
    finally:
        pool.shutdown(wait=False)  # the futures still finish; no new work is accepted


def _parallel_result(future: Any) -> str:
    try:
        return str(future.result())
    except Exception as exc:  # noqa: BLE001 - the same shape a failing tool returns in sequence
        return f"Error: {type(exc).__name__}: {exc}"


def _assistant_turn_for(provider: "BaseProvider", reply: AssistantMessage) -> Message:
    """The assistant message to record for a native tool round, listing exactly the calls that
    will get a tool result (``reply.tool_calls`` after dedupe) under the same ids.

    The host's own message (``reply.raw``) is kept when possible, since it can carry fields the
    next request needs (reasoning content, provider ids); its ``tool_calls`` are rewritten so a
    dropped duplicate or a call that came back without an id cannot leave an unanswered id."""
    raw = reply.raw if isinstance(reply.raw, dict) else None
    if not raw or not isinstance(raw.get("tool_calls"), list):
        return provider.assistant_tool_turn(reply)
    by_id = {str(item.get("id")): item for item in raw["tool_calls"] if isinstance(item, dict) and item.get("id")}
    rows: list[dict[str, Any]] = []
    for call in reply.tool_calls:
        item = by_id.get(str(call.id))
        if item is None:
            item = {
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(_slim_args_for_history(call.arguments), ensure_ascii=False),
                },
            }
        rows.append({**item, "id": call.id})
    return {**raw, "tool_calls": rows}


def _with_auto_restart(text: str) -> str:
    """If CatCrewAI source was patched this turn and she forgot restart_catcrew, do it."""
    try:
        from backend.services.pending_restart import maybe_auto_restart_after_edits

        auto = maybe_auto_restart_after_edits()
    except Exception:  # noqa: BLE001
        return text
    if not auto:
        return text
    base = (text or "").strip()
    if not base:
        return auto
    return f"{base}\n\n{auto}"




def _strip_printed_tools(content: str | None) -> str:
    """Keep spoken/prose bits; drop tool_code fences so they are not re-sent."""
    text = _FENCE_BLOCK.sub("", content or "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:2_000]


def _text_tool_results_user(pairs: list[tuple[ToolCall, str]]) -> str:
    blocks: list[str] = []
    for call, result in pairs:
        slim = _slim_args_for_history(call.arguments)
        blocks.append(
            f"### {call.name}\n"
            f"args: {json.dumps(slim, ensure_ascii=False)}\n"
            f"result:\n{result}"
        )
    return (
        "Tool results from your last printed tool_code / JSON calls "
        "(already executed — do not repeat the same call):\n\n"
        + "\n\n".join(blocks)
        + "\n\nContinue the task with more tools if needed. "
        "Do not claim the project is done until tool results confirm it."
    )


def _call_key(call: ToolCall) -> tuple[str, str]:
    args = dict(call.arguments or {})
    if call.name == "delegate_to_bot":
        return (call.name, str(args.get("bot_id") or args.get("bot") or "").strip().lower())
    for field in ("path", "cwd", "src", "dest"):
        value = args.get(field)
        if isinstance(value, str) and value.strip():
            args[field] = value.replace("/", "\\").rstrip("\\").lower()
    return (
        call.name,
        json.dumps(args, sort_keys=True, ensure_ascii=False),
    )


def _failed_tool_result(result: str, name: str = "") -> bool:
    """See ``harness/tool_result.tool_failed``: a non-zero exit, a timeout or a gate refusal is a
    failure too, not only an ``Error:`` prefix."""
    from backend.harness.tool_result import tool_failed

    return tool_failed(name, result or "")


def _repeat_skip_text(call: ToolCall, previous: str) -> str:
    if _failed_tool_result(previous, call.name):
        earlier = " ".join(previous.split())[:400]
        return (
            f"Skipped: {call.name} already failed with these arguments and nothing has "
            f"changed since (no file written, no command run). Earlier result: {earlier}\n"
            "Fix the cause first (write or patch a file, install a dependency), then the "
            "same call is allowed again; otherwise use different arguments or a "
            "different tool."
        )
    return (
        f"Skipped: {call.name} already ran with these arguments and nothing has changed "
        "since (no file written, no command run), so the result is the same — use the "
        "earlier tool result. Make a change first, or take a different step."
    )


def _replace_fail_steer(result: str) -> str | None:
    """The nudge after a failed replace_in_file. When the error itself names the right file
    (``Error: wrong file …`` with the locations), point at those paths; otherwise ask for a read
    or a search before the next attempt. No file names are baked in here: the tool's error text
    carries them, for whatever project this is."""
    text = result or ""
    if not _failed_tool_result(text, "replace_in_file"):
        return None
    if text.lower().startswith("error: wrong file"):
        return (
            "That file is the wrong place for this change. The error above lists the right files "
            "and lines: call replace_in_file on those next, and do not retry the same file. "
            "Do not print tools in chat."
        )
    return (
        "replace_in_file failed. Use the Error text: read the file or search_files "
        "for a unique fragment, then patch with the exact old_string. "
        "Do not retry the same arguments."
    )


def _replace_fail_steer_from_pairs(pairs: list[tuple[ToolCall, str]]) -> str | None:
    for call, result in pairs:
        if call.name == "replace_in_file" and _failed_tool_result(result, call.name):
            return _replace_fail_steer(result)
    return None


# Rounds in a row where every requested call was an unchanged repeat before the turn is
# ended. Three, not two: the first steer is often ignored by small local models.
_MAX_SKIP_STREAK = 3

_REPEAT_STEER = (
    "Nothing has changed since you ran that exact call, so it would return the same "
    "result. Change the situation first (edit a file, install the missing dependency, "
    "fix the cause named in the error), or use a different tool. If you are blocked on "
    "something your tools cannot do, stop and say exactly what in your reply, and call "
    "report_task_results with status blocked. Do not revive an older finished task."
)


_CUT_OFF_STEER = (
    "Your last reply hit the model's output limit in the middle of a tool call, so nothing was "
    "run. Do the same step in smaller pieces: write a big file in parts (write_file for the "
    "first part, then replace_in_file or more write_file calls), and make one call per reply."
)


_MAX_CUT_OFFS = 2


def _cut_off_text(provider: Any) -> str:
    model = str(getattr(provider, "model", "") or "the model")
    return (
        f"[{model} kept hitting its output limit in the middle of a tool call, so nothing was "
        "run. Ask for a smaller step, or raise the model's max output tokens on the Models page.]"
    )


def _cut_off(provider: Any) -> bool:
    """True when the last model call ended because it ran out of output tokens."""
    return str(getattr(provider, "last_finish", "") or "").lower() in {"length", "max_tokens"}


_EMPTY_NUDGE = (
    "Your last reply was empty. Answer the user in plain text now, or call one of your tools. "
    "Do not reply with nothing."
)


def _empty_reason(provider: Any) -> str:
    finish = str(getattr(provider, "last_finish", "") or "")
    thought = int(getattr(provider, "last_reasoning_chars", 0) or 0)
    bits = []
    if finish:
        bits.append(f"finish: {finish}")
    if thought:
        bits.append(f"{thought} characters of reasoning, no answer")
    return ", ".join(bits) or "no detail from the host"


def _empty_text(provider: Any) -> str:
    """What the turn says when the model returned nothing twice. Never an empty string."""
    model = str(getattr(provider, "model", "") or "the model")
    return (
        f"[{model} returned an empty reply twice ({_empty_reason(provider)}). Nothing was done. "
        "Send the message again, or pick a different model for this role on the Models page.]"
    )


def _stuck_text(last: tuple[str, str] | None) -> str:
    """The message that ends a turn stuck on repeats. Names the tool and its last result so
    whoever reads it (the user, or the lead planning a fix) sees the cause, not just the fact."""
    tail = "Send continue and I will try a different approach for your latest request."
    if not last:
        return "I got stuck repeating the same failed tool. " + tail
    name, previous = last
    detail = " ".join((previous or "").split())[:300]
    return (
        f"I got stuck repeating the same failed tool ({name}"
        + (f": {detail}" if detail else "")
        + "). "
        + tail
    )
_EXPLORE_TOOLS = frozenset(
    {"search_files", "web_search", "web_fetch", "glob_files", "list_dir"}
)
def _workspace_is_empty() -> bool:
    """True when the bound computer folder holds no files yet (hidden entries do not count)."""
    try:
        from backend.services.workspace import current_workspace

        root = current_workspace()
        return not any(not entry.name.startswith(".") for entry in root.iterdir())
    except Exception:  # noqa: BLE001 - no workspace bound: no reason to steer
        return False


_EXPLORE_STEER = (
    "Stop exploring. The computer folder is empty: create the project now -- "
    "scaffold_project with the framework's generator (target folder {dir}) for a "
    "framework with a skeleton, or write package.json / requirements.txt yourself for a "
    "plain stack -- then install non-interactively. "
    "Do not search_files or web_fetch again until files exist."
)
_DELEGATE_TOOLS = frozenset({"delegate_to_bot", "run_subagent"})
_REDELEGATE_STEER = (
    "That exact delegate already ran this turn. Call delegate_to_bot with a "
    "DIFFERENT bot_id (see list_roster), or do the work yourself with your own tools. "
    "Then list_dir to verify the result on disk."
)


def fallback_provider(error: ModelUnavailable, *, announce: bool = True) -> BaseProvider | None:
    """The provider for the Fallback role, or ``None`` when it is unset, is the very model that was
    turned away, or cannot be built (no key). A successful switch clears the project's model block,
    so the pipeline does not stop between phases for a model it no longer depends on."""
    try:
        from backend.core import provider_factory
        from backend.harness import model_block
        from backend.harness.events import emit_activity
        from backend.platform.runtime import current_project_id
        from backend.services.model_resolver import get_effective_model

        project_id = current_project_id()
        eff = get_effective_model(project_id, "fallback")
        if not eff.host or not eff.model:
            return None
        if (eff.host, eff.model) == (str(error.host or ""), str(error.model or "")):
            return None
        provider = provider_factory.provider_from_brain(eff.host, eff.model)
    except Exception:  # noqa: BLE001 - no fallback is the old behaviour, never a new failure
        return None
    if not announce:
        return provider
    model_block.clear(project_id)
    model_block.remember_failover(project_id, str(error.host or ""), str(error.model or ""))
    reason = "out of credit" if error.kind == "no_credit" else "rate-limited"
    print(f"[agent] {error.model} is {reason}; switching to the fallback {eff.model}", flush=True)
    emit_activity("Model fallback", f"{error.model} is {reason}; using {eff.model}"[:88])
    return provider


def _sticky_fallback(provider: BaseProvider) -> BaseProvider | None:
    """The Fallback provider when this provider's model was turned away in this project a moment
    ago (``model_block.recently_failed_over``), else ``None``."""
    try:
        from backend.harness import model_block
        from backend.platform.runtime import current_project_id

        host = str(getattr(provider, "host_id", "") or "")
        model = str(getattr(provider, "model", "") or "")
        if not host or not model or not model_block.recently_failed_over(current_project_id(), host, model):
            return None
        return fallback_provider(
            ModelUnavailable("recently turned away", host=host, model=model, status=429, kind="rate_limited"),
            announce=False,
        )
    except Exception:  # noqa: BLE001
        return None

