"""Removed — the local GGUF folder/models directory no longer exists.

CatCrewAI no longer keeps its own models folder (``C:\\CatCrewAI\\models``) or
scans it for GGUF files; LM Studio owns model storage entirely. See
``providers/lmstudio.py`` and ``lm_runtime.probe_lmstudio()``.

Nothing in the app imports this module any more. It is kept as an inert
placeholder only because the environment this change was made from could
not delete files on the user's machine (the device shell has been down
since the Sept 8 Windows update). Safe to delete this file by hand.
"""
