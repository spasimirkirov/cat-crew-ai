"""Agent isolation for remote users: each regular user's commands run in their own container.

Remote access lets people other than the server's owner instruct the cats, and the cats run shell
commands. A regular user's work therefore has to run in a container that sees only that user's
workspace. Until that is in place this module reports "not ready", and the API refuses invites
for regular users (``api/routes/auth.py``); admins are trusted with the whole server.
"""

from __future__ import annotations

from typing import Any


def status() -> dict[str, Any]:
    """``{"ready": bool, "detail": str}``: can regular users' agents run isolated?"""
    return {"ready": False, "detail": "the Docker sandbox for user workspaces is not set up yet."}
