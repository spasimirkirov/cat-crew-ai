"""Seed the Arcane test team (Jayce + specialists). Photos stay empty.

Run from the repo root:

    python scripts/init_arcane_team.py

Merges into the current catalog and keeps any photos you already set.
Pass ``--replace-catalog`` to wipe other teams/members and keep only Arcane.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jinx_ai.platform.arcane import TEAM_NAME, seed_arcane
from jinx_ai.platform.registry import STORE_PATH


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replace-catalog",
        action="store_true",
        help="Replace the whole catalog with Arcane (Chief stays default).",
    )
    args = parser.parse_args()
    registry = seed_arcane(replace_catalog=args.replace_catalog)
    team = registry.teams.get("arcane")
    print(f"Team {TEAM_NAME}: {len(team.bot_ids) if team else 0} members")
    for bot_id in team.bot_ids if team else []:
        bot = registry.bots.get(bot_id)
        if bot is None:
            continue
        photo = "photo set" if bot.avatar else "no photo yet"
        print(f"  {bot.name}  ({bot.title})  [{photo}]")
    print("Wrote", STORE_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
