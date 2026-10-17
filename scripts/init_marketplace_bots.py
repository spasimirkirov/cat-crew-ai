"""Initialize the example multi-bot catalog (Software Engineering + Accountant).

Run from the repo root:

    python scripts/init_marketplace_bots.py

This is the same catalog shipped in ``backend/platform/defaults.json``.
Re-run with ``--overwrite`` to replace ``data/platform/registry.json``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.platform.chief import DEFAULT_CHIEF_PROMPT
from backend.platform.presets import (
    ACCOUNTANT_PROMPT,
    ARCHITECT_PROMPT,
    DESIGNER_PROMPT,
    DEVELOPER_PROMPT,
    QA_PROMPT,
)
from backend.platform.registry import PlatformBuilder, load_registry


def build_marketplace() -> PlatformBuilder:
    """Software Engineering + Accountant roles + Simple Marketplace Website."""
    plat = PlatformBuilder()
    plat.global_tools("read_skill", "system_time")
    # Optional: plat.global_mcp("github")  — every bot inherits enabled connectors.
    plat.chief(name="Cat Crew", prompt=DEFAULT_CHIEF_PROMPT, max_rounds=12)

    plat.bot(
        "architect",
        "Architect",
        role="architect",
        prompt=ARCHITECT_PROMPT,
        lead=True,
        description="Plans specs and system shape.",
    )
    plat.bot(
        "designer",
        "Designer",
        role="designer",
        prompt=DESIGNER_PROMPT,
        description="UX and static UI.",
        mcp_tools=[],  # per-bot MCP; empty = inherit global_mcp only
    )
    plat.bot(
        "developer",
        "Developer",
        role="developer",
        prompt=DEVELOPER_PROMPT,
        description="Implements the product in the workspace.",
    )
    plat.bot(
        "qa",
        "QA",
        role="qa",
        prompt=QA_PROMPT,
        description="Tests and regression notes.",
    )

    plat.bot(
        "accountant",
        "Accountant",
        role="accountant",
        prompt=ACCOUNTANT_PROMPT,
        lead=True,
        description="Numbers and commercial constraints.",
        inherit_global_mcp=True,
    )

    plat.project(
        "marketplace",
        "Simple Marketplace Website",
        "A small marketplace: listings, cart, checkout, and seller payouts.",
        bots=["architect", "designer", "developer", "qa", "accountant"],
        state={"goal": "Ship a simple marketplace website."},
    )
    return plat


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing data/platform/registry.json",
    )
    args = parser.parse_args()
    current = load_registry(refresh=True)
    if current.projects and not args.overwrite:
        print(
            f"Catalog already has {len(current.projects)} project(s) at "
            "data/platform/registry.json. Pass --overwrite to replace."
        )
        return 0
    registry = build_marketplace().save()
    print(f"Chief: {registry.chief.name} (not staffed on any project)")
    for project in registry.projects.values():
        members = ", ".join(
            registry.bots[bid].name for bid in project.bot_ids if bid in registry.bots
        )
        print(f"Project {project.name}: bots={members}")
    from backend.platform.registry import STORE_PATH

    print("Wrote", STORE_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
