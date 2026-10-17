"""Removed — the Hugging Face Hub GGUF browser no longer exists.

LM Studio owns model discovery and downloading now (its own in-app model
catalog); CatCrewAI no longer searches Hugging Face for GGUF families, scans
``C:\\CatCrewAI\\models``, or downloads anything itself. See
``providers/lmstudio.py`` and ``lm_runtime.probe_lmstudio()``.

Nothing in the app imports this module any more. It is kept as an inert
placeholder only because the environment this change was made from could
not delete files on the user's machine (the device shell has been down
since the Sept 8 Windows update). Safe to delete this file by hand.
"""
