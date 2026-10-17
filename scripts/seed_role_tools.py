"""Seed Phase 15's example role-specific Letta-style tools (Task #22 of the
Dual Skill/Tool Architecture — see the CatCrewAI project doc
``architecture/phase15-dual-skill-tool-architecture.md``).

Registers each function in ``backend/tool_functions/`` into the ``tools``
catalog (``platform/db.py``) via ``platform/tool_registry.py``, then
assigns each one only to its role's bot — Pulse (developer), Forge
(devops), Aegis (qa), and Pixel (designer, browser tools only) — the per-agent
tool matrix: a bot gets the registry tools assigned to it (plus any
``is_global`` ones) on top of its built-in role pack.

The app now does this itself at start-up (``platform/role_tools.py``,
``ensure_role_tools``), so this script is only a manual reset: it re-assigns
every role tool to its cat, including ones you removed in the Tools page.
Safe to re-run: ``upsert_tool`` and ``assign_tool_by_slug`` (``INSERT OR
IGNORE`` on a ``UNIQUE(agent_id, tool_id)`` constraint) are both idempotent.

Run from the repo root:

    python scripts/seed_role_tools.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.db import ledger as db
from backend.platform.role_tools import ensure_role_tools


def main() -> None:
    db.init_db()
    for note in ensure_role_tools(force=True):
        print(note)

    print("\nCurrent tools catalog:")
    for row in db.list_tools():
        agents = sorted(db.agent_ids_for_tool(row["id"]))
        print(f"  {row['slug']:<28} global={bool(row['is_global'])!s:<6} assigned={agents}")


if __name__ == "__main__":
    main()
