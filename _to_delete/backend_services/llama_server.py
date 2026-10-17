"""Removed — CatCrewAI no longer manages a local llama-server process.

The AI Models → LM Studio purge replaced this entire module: CatCrewAI talks to
LM Studio (a separate application the user runs and manages themselves —
loading models, starting its local server) via ``providers/lmstudio.py``
and ``lm_runtime.probe_lmstudio()`` instead of spawning, health-checking,
or stopping a llama-server subprocess itself.

Nothing in the app imports this module any more. It is kept as an inert
placeholder only because the environment this change was made from could
not delete files on the user's machine (the device shell has been down
since the Sept 8 Windows update — see architecture/engine-rebuild-status.md
and architecture/phase2-observability-and-fixes.md in the project). It is
safe to delete this file by hand once you can.
"""
