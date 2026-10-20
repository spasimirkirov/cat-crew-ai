"""Sign-in for remote access: status, first-admin registration, login, invites and users."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.api import access
from backend.services import auth, sandbox

router = APIRouter(prefix="/api", tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SetupBody(Credentials):
    setup_code: str = Field(default="", max_length=64)


class PasswordChange(BaseModel):
    old: str = Field(min_length=1, max_length=256)
    new: str = Field(min_length=1, max_length=256)


class InviteBody(BaseModel):
    role: str = "user"
    hours: float = Field(default=auth.INVITE_HOURS, gt=0, le=24 * 30)


class RoleBody(BaseModel):
    role: str


def _secure(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip() == "https"


def _sign_in(request: Request, response: Response, user: auth.User) -> dict:
    token = auth.create_session(user.id)
    response.set_cookie(
        access.COOKIE,
        token,
        max_age=auth.SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=_secure(request),
        path="/",
    )
    return {"user": user.public()}


def _need_remote() -> None:
    if not access.remote_mode():
        raise HTTPException(409, "CatCrewAI runs locally: there is nothing to sign in to.")


@router.get("/auth/status")
def status() -> dict:
    """What the front end shows first: nothing (local), the first-admin setup, or the login."""
    if not access.remote_mode():
        return {"mode": "local", "needs_setup": False, "user": auth.LOCAL_ADMIN.public()}
    user = access._user.get()
    return {"mode": "remote", "needs_setup": auth.needs_setup(), "user": user.public() if user else None}


@router.post("/auth/register")
def register(body: SetupBody, request: Request, response: Response) -> dict:
    """The first account becomes the admin (with the setup code from the server console); after
    that, registration needs an invite."""
    _need_remote()
    try:
        user = auth.register_first_admin(body.username, body.password, body.setup_code)
    except auth.AuthError as exc:
        raise HTTPException(403 if ("closed" in str(exc) or "setup code" in str(exc)) else 422, str(exc)) from exc
    _adopt_unowned_projects(user)
    return _sign_in(request, response, user)


def _adopt_unowned_projects(user: auth.User) -> None:
    """Projects made before remote access existed belong to the first admin."""
    from backend.platform.registry import load_registry, save_registry

    registry = load_registry(refresh=True)
    changed = False
    for project in registry.projects.values():
        if not getattr(project, "owner_id", ""):
            project.owner_id = user.id
            changed = True
    if changed:
        save_registry(registry)


@router.post("/auth/login")
def login(body: Credentials, request: Request, response: Response) -> dict:
    _need_remote()
    address = request.client.host if request.client else ""
    if auth.login_locked(body.username, address):
        raise HTTPException(429, "Too many failed sign-ins: wait a few minutes and try again.")
    user = auth.authenticate(body.username, body.password, address)
    if user is None:
        raise HTTPException(401, "Wrong username or password.")
    return _sign_in(request, response, user)


@router.post("/auth/logout", status_code=204)
def logout(request: Request) -> Response:
    auth.revoke_session(request.cookies.get(access.COOKIE))
    response = Response(status_code=204)
    response.delete_cookie(access.COOKIE, path="/")
    return response


@router.get("/auth/invite/{token}")
def invite_info(token: str) -> dict:
    _need_remote()
    info = auth.invite_info(token)
    if info is None:
        raise HTTPException(404, "This invite is not valid any more: ask an admin for a new one.")
    return info


@router.post("/auth/invite/{token}")
def accept_invite(token: str, body: Credentials, request: Request, response: Response) -> dict:
    _need_remote()
    try:
        user = auth.redeem_invite(token, body.username, body.password)
    except auth.AuthError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _sign_in(request, response, user)


@router.post("/auth/password", status_code=204)
def change_password(body: PasswordChange, response: Response) -> Response:
    _need_remote()
    user = access.current_user()
    try:
        auth.change_password(user.id, body.old, body.new)
    except auth.AuthError as exc:
        raise HTTPException(422, str(exc)) from exc
    out = Response(status_code=204)
    out.delete_cookie(access.COOKIE, path="/")  # every session ended: sign in again
    return out


# -- admin: users and invites (the paths are admin-only in access.ADMIN_ONLY) ---------------


@router.get("/users")
def list_users() -> dict:
    _need_remote()
    return {
        "users": [u.public() for u in auth.list_users()],
        "invites": auth.list_invites(),
        "sandbox": sandbox.status(),
    }


@router.post("/users/invites", status_code=201)
def create_invite(body: InviteBody, request: Request) -> dict:
    """A one-time sign-up link. A regular user's agents must run isolated, so their invites need
    the sandbox (``services/sandbox.py``) to be ready; admins are trusted with the whole server."""
    _need_remote()
    if body.role == "user" and not sandbox.status()["ready"]:
        raise HTTPException(409, f"Regular users need agent isolation first: {sandbox.status()['detail']}")
    try:
        token = auth.create_invite(access.current_user().id, body.role, body.hours)
    except auth.AuthError as exc:
        raise HTTPException(422, str(exc)) from exc
    base = str(request.base_url).rstrip("/")
    return {"token": token, "url": f"{base}/#/invite/{token}", "role": body.role}


@router.patch("/users/{user_id}")
def set_role(user_id: str, body: RoleBody) -> dict:
    _need_remote()
    try:
        return auth.set_role(user_id, body.role).public()
    except auth.AuthError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/users/{user_id}", status_code=204)
def remove_user(user_id: str) -> Response:
    _need_remote()
    if user_id == access.current_user().id:
        raise HTTPException(422, "You cannot remove your own account.")
    try:
        auth.disable_user(user_id)
    except auth.AuthError as exc:
        raise HTTPException(422, str(exc)) from exc
    return Response(status_code=204)
