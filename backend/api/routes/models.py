"""Model roles and hosts. Never returns an API key or token."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.api import deps
from backend.services import model_discovery, model_health, model_resolver, settings_store
from backend.services.api_access import CLOUD_HOSTS, has_api_key, is_cloud_host, key_source
from backend.services.lm_runtime import HostSnapshot, host_label

router = APIRouter(prefix="/api", tags=["models"])

# wire role name -> settings role
_ROLES = {
    "chat": "companion",
    "coding": "coding",
    "memory": "memory",
    "vision": "vision",
    "planning": "planning",
    "review": "review",
    "images": "image",
}


class RoleBrain(BaseModel):
    host: str = ""
    model: str = ""


class RolesBody(BaseModel):
    chat: RoleBrain | None = None
    coding: RoleBrain | None = None
    memory: RoleBrain | None = None
    vision: RoleBrain | None = None
    planning: RoleBrain | None = None
    review: RoleBrain | None = None
    images: RoleBrain | None = None


def _state() -> dict:
    roles = {}
    for wire, role in _ROLES.items():
        host, model = settings_store.saved_role_brain(role)
        roles[wire] = {"host": host, "model": model}
    enabled = set(settings_store.enabled_hosts())
    hosts = []
    for kind in CLOUD_HOSTS:
        hosts.append(
            {
                "id": kind,
                "label": host_label(kind),
                "enabled": kind in enabled,
                "cloud": True,
                "configured": has_api_key(kind),  # a boolean, never the key
                "key_source": key_source(kind),  # "saved" | "environment" | "none"
            }
        )
    return {"roles": roles, "hosts": hosts}


@router.get("/models")
def get_models() -> dict:
    return _state()


@router.put("/models/roles")
def put_roles(body: RolesBody) -> dict:
    for wire, brain in body.model_dump(exclude_none=True).items():
        host = str(brain.get("host") or "").strip().lower()
        if host and host not in {"comfyui", "off"} and not is_cloud_host(host):
            raise HTTPException(422, f"Unknown host {host!r}.")
        settings_store.save_role_brain(_ROLES[wire], host, str(brain.get("model") or ""))
    model_health.invalidate()  # the picks changed: the next health check looks at the new ones
    return _state()


class ReplaceBody(BaseModel):
    from_host: str
    from_model: str
    to_host: str
    to_model: str


@router.post("/models/replace")
def replace_model(body: ReplaceBody) -> dict:
    """Switch every role that runs on one model (globally and in every project) to another: the
    one-click way out of a model that is rate-limited or out of credit."""
    to_host = body.to_host.strip().lower()
    if not is_cloud_host(to_host):
        raise HTTPException(422, f"Unknown host {to_host!r}.")
    if not has_api_key(to_host):
        raise HTTPException(422, f"{host_label(to_host)} has no API key. Add one under Models → Hosts first.")
    if not body.to_model.strip():
        raise HTTPException(422, "Pick the model to switch to.")
    changed = settings_store.replace_model_everywhere(body.from_host, body.from_model, to_host, body.to_model)
    model_health.invalidate()
    return {"changed": changed, **_state()}


def _known_host(host: str) -> str:
    if not is_cloud_host(host):
        raise HTTPException(404, f"Unknown host {host!r}.")
    return host


def _host_view(host: str, snap: HostSnapshot) -> dict:
    return {
        "host": host,
        "online": bool(snap.online),
        "error": snap.error,  # a short fixed phrase: it never carries a URL, a key or a raw exception
        "error_kind": snap.error_kind,
        "checked_at": snap.checked_at,
        "models": [
            {
                "name": m.name,
                "display_name": m.display_name,
                "loaded": bool(m.loaded),
                "capabilities": list(m.capabilities),
                "context_length": m.context_length,
                "price_prompt_per_1m": m.price_prompt_per_1m,
                "price_completion_per_1m": m.price_completion_per_1m,
                "is_free": bool(m.is_free),
                "supports_prompt_cache": bool(m.supports_prompt_cache),
            }
            for m in snap.models
        ],
    }


@router.get("/models/hosts/{host}")
async def host_models(host: str, image: bool = False) -> dict:
    """The host's model list (a short-lived cache; a network probe runs off the event loop when it is
    stale). ``image=true`` asks OpenRouter for its image-generation models instead of its chat models
    -- the Images role's own picker, a completely different list behind the same host id."""
    host = _known_host(host)
    return _host_view(host, await asyncio.to_thread(model_discovery.cached_snapshot, host, variant="image" if image else ""))


@router.post("/hosts/{host_id}/refresh-models")
async def refresh_models(host_id: str, image: bool = False) -> dict:
    """Ask the provider for its live model list right now and re-check the saved role assignments."""
    host = _known_host(host_id)
    snap = await asyncio.to_thread(model_discovery.cached_snapshot, host, force=True, variant="image" if image else "")
    model_health.invalidate()
    return _host_view(host, snap)


@router.get("/models/health")
async def models_health(refresh: bool = False) -> dict:
    """Are the models chosen for the roles still available? (See ``services/model_health.py``.)"""
    return (await asyncio.to_thread(model_health.current, refresh=refresh)).to_dict()


# -- per-project overrides -----------------------------------------------------------------------


class ProjectRolePick(BaseModel):
    """One role's pick for a project. Host ``"inherit"`` (or empty) means "Use Global Setting"."""

    host: str = "inherit"
    model: str = ""


class ProjectModelsBody(BaseModel):
    roles: dict[str, ProjectRolePick | None]


def _project_models_state(project_id: str) -> dict:
    data = settings_store.load_settings()
    overrides = model_resolver.overrides_for_project(project_id, data)
    effective: dict[str, dict] = {}
    global_effective: dict[str, dict] = {}
    for wire in model_resolver.WIRE_ROLES:
        eff = model_resolver.get_effective_model(project_id, wire, data=data)
        effective[wire] = eff.to_dict()  # never carries a key
        # What the role runs on with no project override at all: the "Use Global Setting" label.
        global_effective[wire] = model_resolver.get_effective_model("", wire, data=data).to_dict()
    global_roles = {}
    for wire, role in _ROLES.items():
        host, model = settings_store.saved_role_brain(role)
        global_roles[wire] = {"host": host, "model": model}
    base = _state()
    return {
        "project_id": project_id,
        "overrides": overrides,  # only the roles this project overrides
        "effective": effective,  # what each role runs on now (project pick, else global)
        "global": global_roles,  # the Models page picks as saved (an unset role is empty)
        "global_effective": global_effective,  # what each role runs on globally (unset roles follow Chat)
        "hosts": base["hosts"],
    }


@router.get("/projects/{project_id}/models")
def get_project_models(project_id: str) -> dict:
    deps.need_project(project_id)
    return _project_models_state(project_id)


@router.put("/projects/{project_id}/models")
def put_project_models(project_id: str, body: ProjectModelsBody) -> dict:
    """Save this project's picks. ``{"roles": {"coding": {"host": "openrouter", "model": "deepseek/deepseek-v4-pro"},
    "vision": {"host": "inherit"}}}``: named roles change, the rest stay as they are."""
    deps.need_project(project_id)
    picks: dict[str, dict[str, str] | None] = {}
    for wire, pick in body.roles.items():
        if wire not in model_resolver.WIRE_ROLES:
            raise HTTPException(422, f"Unknown role {wire!r}.")
        host = (pick.host if pick else "inherit").strip().lower()
        if host in ("", model_resolver.INHERIT):
            picks[wire] = None
            continue
        if wire == "vision" and host == "off":
            picks[wire] = {"host": "off", "model": ""}
            continue
        if wire == "images":
            if host not in ("comfyui", "openrouter", "off"):
                raise HTTPException(422, "Images run on ComfyUI or OpenRouter, or are Off.")
            if host == "off":
                picks[wire] = {"host": "off", "model": ""}
                continue
        elif not is_cloud_host(host):
            raise HTTPException(422, f"Unknown host {host!r}.")
        if not (pick and pick.model.strip()):
            raise HTTPException(422, f"Pick a model for {wire}, or choose Use Global Setting.")
        picks[wire] = {"host": host, "model": pick.model.strip()}
    model_resolver.set_project_overrides(project_id, picks)
    return _project_models_state(project_id)
