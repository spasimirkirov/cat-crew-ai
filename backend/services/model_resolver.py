"""Which host and model a role runs on for a project: project override, else the global setting.

Cascade, for one project and one role (Chat, Coding, Memory, Vision, Planning, Images):

1. the project's own pick for that role (Project Settings), when it names a host that can answer
   now (an enabled host, or ComfyUI for images) and a model;
2. the global pick from Models (``settings.json`` -> ``brains``), which is also where "Use Global
   Setting" lands;
3. for Coding and Memory (and the roles that "follow Chat"): the Chat role, resolved the same way,
   so a project's Chat override also moves the roles that were left unset.

Every place that builds a provider asks this module (``core/provider_factory.py``), so a change on
the Project Settings page applies from the next turn, with no restart.

The result carries the API key for callers that need to talk to the host directly. It is kept out
of ``repr()`` and ``to_dict()`` (nothing in this codebase logs or returns a key).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.services import settings_store
from backend.services.api_access import is_cloud_host, resolved_key

# The names the API and the UI use for the six roles, and the settings role behind each.
WIRE_ROLES: dict[str, str] = {
    "chat": "companion",
    "coding": "coding",
    "memory": "memory",
    "vision": "vision",
    "planning": "planning",
    "review": "review",
    "fallback": "fallback",
    "images": "image",
}
INHERIT = "inherit"
#: The host of a role the user switched off (Images, Vision): nothing runs it.
OFF = "off"
_FOLLOWS_CHAT = {"coding", "memory", "vision", "planning"}


@dataclass(frozen=True)
class EffectiveModel:
    host: str
    model: str
    #: "project" (a Project Settings override), "global" (Models), or "chat" (the role is unset and follows Chat)
    source: str
    #: The user gave this role its own model (else it just follows Chat). Vision and Planning only switch models when true.
    explicit: bool = True
    api_key: str = field(default="", repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        """Wire shape. Never includes the key."""
        return {"host": self.host, "model": self.model, "source": self.source, "explicit": self.explicit}


def _key_for(host: str) -> str:
    return resolved_key(host) if is_cloud_host(host) else ""


def _make(host: str, model: str, source: str, explicit: bool) -> EffectiveModel:
    return EffectiveModel(host=host, model=model, source=source, explicit=explicit, api_key=_key_for(host))


def _usable(role: str, host: str, data: dict[str, Any] | None) -> bool:
    """A project override only counts while its host can answer (a removed key must not strand the project)."""
    if host == OFF:
        return role in ("image", "vision")
    if role == "image":
        return host == "comfyui"
    return host in settings_store.enabled_hosts(data)


def project_override(project_id: str, role: str, data: dict[str, Any] | None = None) -> dict[str, str] | None:
    role = settings_store.normalize_model_role(role)
    brain = settings_store.project_role_overrides(project_id, data).get(role)
    if brain and brain.get("host") != INHERIT and _usable(role, brain["host"], data):
        return brain
    return None


def get_effective_model(
    project_id: str,
    role: str,
    session: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> EffectiveModel:
    """The model ``role`` runs on in ``project_id``. ``project_id`` may be empty (global only)."""
    role = settings_store.normalize_model_role(WIRE_ROLES.get(str(role or "").strip().lower(), role))
    data = data if data is not None else settings_store.load_settings()
    brain = project_override(project_id, role, data)
    if brain:
        return _make(brain["host"], brain["model"], "project", True)

    saved_host, saved_model = settings_store.saved_role_brain(role, data)
    if role == "vision" and saved_host == OFF:
        return _make(OFF, "", "global", True)
    if role == "fallback" and not (saved_host and saved_model):
        # Unset: no failover (never a silent switch to a model the person did not pick).
        return EffectiveModel(host="", model="", source="global", explicit=False)
    if role == "review" and not (saved_host and saved_model):
        # Unset: QA checks with the model the code was written with (Coding, which may follow Chat).
        coding = get_effective_model(project_id, "coding", session, data)
        return _make(coding.host, coding.model, "coding" if coding.source == "global" else coding.source, False)
    if role in _FOLLOWS_CHAT and not (saved_host and saved_model):
        # Unset: follows Chat, and so follows the project's Chat override too.
        chat = get_effective_model(project_id, "companion", session, data)
        return _make(chat.host, chat.model, "chat" if chat.source == "global" else chat.source, False)
    host, model = settings_store.resolve_role_brain(role, session, data)
    return _make(host, model, "global", True)


def overrides_for_project(project_id: str, data: dict[str, Any] | None = None) -> dict[str, dict[str, str]]:
    """The stored overrides by wire role, ``{"coding": {"host", "model"}}``; unset roles are absent."""
    stored = settings_store.project_role_overrides(project_id, data)
    return {wire: stored[role] for wire, role in WIRE_ROLES.items() if role in stored}


def set_project_overrides(project_id: str, roles: dict[str, dict[str, str] | None]) -> dict[str, dict[str, str]]:
    """Save picks by wire role; ``None`` / host ``"inherit"`` / an empty host clears a role."""
    mapped = {WIRE_ROLES[wire]: brain for wire, brain in roles.items() if wire in WIRE_ROLES}
    settings_store.save_project_role_overrides(project_id, mapped)
    return overrides_for_project(project_id)
