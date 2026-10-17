"""The async execution engine — how MCP tools and delegated worker turns
run without blocking or crashing the API server's event loop.

See the Master Architecture Document, section 5, for the full design.
Nothing in this package imports the API layer or
``platform/`` — it is deliberately usable and testable on its own; the
seam it exposes for ``platform/runner.py`` etc. to plug into is the
``executor`` callable every :class:`~backend.core.supervisor.RunSupervisor.spawn`
call takes (an ``AgentContext -> str`` coroutine function).

Not part of this pass (tracked separately — see the README): rewiring
``platform/runner.py``, ``platform/chief.py``, and
``harness/subagent.py`` to actually call into this engine instead of
recursing into ``provider.run_agent()`` directly; publishing engine
events to Qt signals; and splitting ``tools.py`` into per-domain
``ToolModule`` implementations (mentioned as a *future* concept in the
Master Architecture Document, section 4, but explicitly out of scope
here).
"""

from __future__ import annotations

from .context import AgentContext
from .engine import CatCrewEngine, LocalInferenceSemaphore
from .supervisor import AgentRunRequest, Executor, RunNotFound, RunSupervisor
from .verify import VerifyOutcome, verify_and_heal

__all__ = [
    "AgentContext",
    "CatCrewEngine",
    "LocalInferenceSemaphore",
    "AgentRunRequest",
    "Executor",
    "RunNotFound",
    "RunSupervisor",
    "VerifyOutcome",
    "verify_and_heal",
]
