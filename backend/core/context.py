"""AgentContext — the one explicit object a running turn carries.

Replaces threading a turn's state through several separate ``ContextVar``
modules (``harness/runtime.py``, ``platform/runtime.py``,
``harness/deferred.py``, ``harness/approvals.py``, plus the ask_user and
provider hooks ``chat_window.py::ChatWorker`` currently pushes by hand —
see the Master Architecture Document, section 4). An ``AgentContext`` is
built once per :class:`~backend.domain.run.AgentRun` and passed by
reference into every tool call and every child spawn; nothing here is
ambient/global, so two runs executing concurrently on the same event loop
can never see each other's state by accident the way two ``ContextVar``
pushes on the same thread could.

Wiring ``platform/runner.py``, ``platform/chief.py``, and
``harness/subagent.py`` to build and pass an ``AgentContext`` instead of
the current ``ContextVar`` tokens is tracked as a separate step (see the
README's "not yet done" list) — nothing here requires that wiring to be
exercised on its own, which is why this module has no dependency on
``platform/`` or ``harness/``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

from ..domain.run import AgentRun

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a hard import cycle
    from ..providers.base import BaseProvider


@dataclass
class AgentContext:
    """Everything one running :class:`AgentRun` needs, held explicitly.

    ``approval_cb``/``ask_user_cb`` are async so a tool call can suspend
    waiting on a human without blocking the engine's event loop — while
    they're awaited the run's status can be reported as
    :attr:`~backend.domain.run.RunStatus.PAUSED`.
    """

    run: AgentRun
    provider: "BaseProvider | None" = None
    abort: asyncio.Event = field(default_factory=asyncio.Event)
    tool_allowlist: frozenset[str] = field(default_factory=frozenset)
    approval_cb: Callable[[str, dict], Awaitable[str]] | None = None
    ask_user_cb: Callable[[str, list[str]], Awaitable[str]] | None = None

    def is_aborted(self) -> bool:
        return self.abort.is_set()
