"""Pure data models for the multi-agent platform.

Nothing in this package touches disk, the network, Qt, or any provider. It is
the vocabulary the rest of the app (state/, core/, platform/) is built from.

History: this package supersedes the dataclass shapes that used to live in
``backend/platform/models.py`` (Bot/Team/Project/ChiefSpec/Registry). The
shapes are kept close to the original — they were sound — but ``to_dict`` /
``from_dict`` JSON glue moves to ``backend/db/state/repositories.py``, and a new
``AgentRun`` model is added to represent a delegated worker as a real object
instead of a nested function call. See ``docs/ARCHITECTURE.md`` for the reasoning.
"""

from __future__ import annotations

from .bot import Bot
from .chief import ChiefSpec
from .project import Project, ProjectEvent
from .registry import Registry
from .run import AgentRun, RunKind, RunStatus
from .team import Team

__all__ = [
    "Bot",
    "ChiefSpec",
    "Project",
    "ProjectEvent",
    "Registry",
    "AgentRun",
    "RunKind",
    "RunStatus",
    "Team",
]
