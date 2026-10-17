"""Build model providers from the saved "brain" settings (headless).

Per-role brains (chat / coding / memory / vision / planning / image) resolved from
``data/settings.json``, with an optional per-project model override. Every model host is a
cloud host reached through ``services.api_access`` (OpenRouter and the vendors' own APIs).

Nothing here returns or logs an API key.
"""

from __future__ import annotations

from typing import Any

from backend.providers import BaseProvider
from backend.services import model_discovery
from backend.services.api_access import is_cloud_host, make_cloud_provider
from backend.services.model_resolver import get_effective_model
from backend.services.settings_store import enabled_hosts, project_lm_model

# The desktop app only had a "developer" mode; the agent role is "coding".
AGENT_ROLE = "coding"


def provider_from_brain(host: str, model: str) -> BaseProvider:
    if is_cloud_host(host):
        return make_cloud_provider(host, model)
    raise RuntimeError(
        "No AI provider is set up for this role. Add a key under Models → Hosts (OpenRouter, OpenAI, "
        "Anthropic, Gemini, DeepSeek, Groq, Mistral or xAI), then pick a model for the role."
    )


def role_is_explicit(role: str, project_id: str = "") -> bool:
    """True when the user picked a host and model for this role (it does not just follow Chat).
    A project's own pick counts."""
    return get_effective_model(project_id, role).explicit


def coding_is_explicit(project_id: str = "") -> bool:
    return role_is_explicit("coding", project_id)


def provider_if_explicit(role: str, project_id: str = "") -> BaseProvider | None:
    """A provider for ``role`` only when the user gave it its own model, else ``None``.
    Vision and Planning use this: unset means "do not switch models"."""
    eff = get_effective_model(project_id, role)
    if not eff.explicit or eff.host == "off":
        return None
    return provider_from_brain(eff.host, eff.model)


def provider_for_role(
    role: str = AGENT_ROLE,
    session: dict[str, Any] | None = None,
    kind: str | None = None,
    project_id: str = "",
) -> BaseProvider:
    sess = session
    if kind and not (role == "coding" and coding_is_explicit(project_id)):
        sess = dict(session or {})
        sess["provider"] = kind
        sess["model_pinned"] = True  # an explicit host for this call
    eff = get_effective_model(project_id, role, sess)
    return provider_from_brain(eff.host, eff.model)


def provider_for_turn(
    session: dict[str, Any] | None,
    project_id: str = "",
    *,
    room: bool = False,
    role: str = AGENT_ROLE,
):
    """Provider for one turn, resolved fresh each turn (project override, else global) so a
    change applies in real time with no restart. ``role`` is Coding for work turns and the
    1:1 chats, Chat for Cat Crew's free conversation."""
    eff = get_effective_model(project_id, role, session)
    provider = provider_from_brain(eff.host, eff.model)
    if room and project_id and eff.source != "project":
        # The older single per-project model (kept for settings files that still have one).
        host, model = project_lm_model(project_id)
        if host and model and host in enabled_hosts():
            provider = provider_from_brain(host, model)
    return provider


def caps_for_current(
    session: dict[str, Any] | None, project_id: str = "", role: str = AGENT_ROLE
) -> tuple[str, str, list[str], bool]:
    """(host, model, capabilities, loaded) for the model a turn will use.

    Capabilities come from a short-TTL cached probe of the host; a probe that
    fails (no network, a bad key) just yields no capabilities, never an error."""
    eff = get_effective_model(project_id, role, session)
    host, model = eff.host, eff.model
    caps: list[str] = []
    loaded = False
    try:
        snap = model_discovery.cached_snapshot(host)
    except Exception:  # noqa: BLE001 - a dead host must not fail the turn
        snap = None
    if snap is not None:
        for item in snap.models:
            if item.name == model:
                caps = list(item.capabilities)
                loaded = bool(item.loaded)
                break
    return host, model, caps, loaded
