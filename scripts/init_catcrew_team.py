"""Migrate off the old "Team Arcane" roster and seed CatCrewAI's official
8-agent core catalog (Cipher, Vanguard, Pixel, Pulse, Aegis, Lex, Blaze,
Forge). Photos stay empty until set in Team Members.

Run from the repo root:

    python scripts/init_catcrew_team.py

This is the CatCrewAI-rebrand successor to ``init_arcane_team.py`` — same
one-time, manually-run convention (the catalog is never auto-seeded at
app startup). It also:

- removes the legacy "Team Arcane" member bots, and repoints any project
  still staffed with them onto the new CatCrewAI roster
  (``platform.catcrew.migrate_legacy_arcane``);
- initializes the SQLite operational ledger (``platform/db.py`` — tasks,
  file changes, skills) if it doesn't exist yet;
- syncs every skill playbook found on disk into the ``skills`` table and
  seeds a few sensible default agent<->skill assignments so the matrix
  isn't empty on first run.

Pass ``--replace-catalog`` to wipe every other team/member and keep only
the CatCrewAI roster.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.db import ledger as db
from backend.platform.catcrew import CATCREW_MEMBERS, ROSTER_NAME, migrate_legacy_arcane, seed_catcrew
from backend.platform.registry import STORE_PATH, load_registry, save_registry

# agent_id -> skill slugs to assign by default. Slugs must exist on disk
# under backend/skills/ (or an overlay root) — anything not found is
# skipped rather than erroring, since a fresh checkout may not carry every
# optional skill.
_DEFAULT_SKILL_ASSIGNMENTS: dict[str, tuple[str, ...]] = {
    "forge": ("docker", "home-lan", "commit-style"),
    "pulse": ("coding-companion", "commit-style", "static-web"),
    "vanguard": ("coding-companion", "web-research"),
    "pixel": ("static-web", "comfy"),
    "cipher": ("web-research",),
    "blaze": ("web-research",),
    "lex": ("web-research",),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replace-catalog",
        action="store_true",
        help="Replace the whole catalog with CatCrewAI (Chief stays default).",
    )
    args = parser.parse_args()

    registry = load_registry(refresh=True)
    notes = migrate_legacy_arcane(registry)
    if notes:
        save_registry(registry)
        for note in notes:
            print("[migrate]", note)
    else:
        print("[migrate] No legacy Arcane team found — nothing to migrate.")

    registry = seed_catcrew(replace_catalog=args.replace_catalog)
    roster_ids = [spec["id"] for spec in CATCREW_MEMBERS]
    print(f"Roster {ROSTER_NAME}: {len(roster_ids)} members")
    for bot_id in roster_ids:
        bot = registry.bots.get(bot_id)
        if bot is None:
            continue
        photo = "photo set" if bot.avatar else "no photo yet"
        print(f"  {bot.name}  ({bot.title})  [{photo}]")
    print("Wrote", STORE_PATH)

    db.init_db()
    synced = db.sync_skills_from_disk()
    print(f"[skills] Synced {synced} playbook(s) from disk into the skill catalog.")
    assigned = 0
    for agent_id, slugs in _DEFAULT_SKILL_ASSIGNMENTS.items():
        if agent_id not in registry.bots:
            continue
        for slug in slugs:
            if db.get_skill_by_slug(slug) is None:
                continue
            db.assign_skill_by_slug(agent_id, slug)
            assigned += 1
    print(f"[skills] Seeded {assigned} default agent<->skill assignment(s).")
    print("Wrote", db.DB_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
