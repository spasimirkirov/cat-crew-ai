"""Look up teammates: names, roles, bios, photos. A tool result — not prompt stuffing."""

from __future__ import annotations

from pathlib import Path

from .models import Bot, Project, Registry


def avatar_path(bot: Bot) -> str:
    raw = (bot.avatar or "").strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if path.is_file():
        return str(path.resolve())
    return raw


def format_member_card(bot: Bot, *, lead: bool = False) -> str:
    """Public dossier for a site or a teammate — not the system prompt."""
    title = (bot.title or bot.role or "").strip() or bot.role
    photo = avatar_path(bot)
    if photo:
        photo_line = (
            f"  photo: {photo}\n"
            "    Copy into the project computer with copy_path for the team page. "
            "Do not invent a different face."
        )
    else:
        photo_line = (
            "  photo: none — initials or a generic role glyph. Do not invent a portrait."
        )
    skills = ", ".join(bot.skills) if bot.skills else "(none listed)"
    lead_mark = " — lead" if lead else ""
    bio = (bot.description or "").strip() or "(no bio)"
    return "\n".join(
        [
            f"- {bot.name}{lead_mark} [{bot.id}]",
            f"  title: {title}",
            f"  role: {bot.role}",
            f"  bio: {bio}",
            f"  skills: {skills}",
            photo_line,
        ]
    )


def format_project_roster(project: Project, registry: Registry) -> str:
    lines = [
        "Official roster — names, titles, roles, bios, photos.",
        "Call teammates by name. Use these cards for a team/about page.",
    ]
    staffed = registry.project_bots(project)
    if not staffed:
        lines.append(
            "(none staffed on this project yet — showing the member catalog. "
            "delegate_to_bot by name staffs that person on this project.)"
        )
        bots = list(registry.bots.values())
        if not bots:
            lines.append("(catalog is empty)")
            return "\n".join(lines)
        for bot in bots:
            lines.append(format_member_card(bot, lead=bot.lead))
        return "\n".join(lines)
    for bot in staffed:
        lines.append(format_member_card(bot, lead=bot.lead))
    return "\n".join(lines)


def _bound_project(project_id: str = "") -> tuple[Registry, Project] | str:
    from .registry import load_registry
    from .runtime import current_project_id

    rid = str(project_id or current_project_id() or "").strip()
    registry = load_registry()
    if not rid:
        return "No project is bound. Open a project first."
    project = registry.projects.get(rid)
    if project is None:
        return f"Unknown project {rid!r}."
    return registry, project


def _member_index_line(bot: Bot, *, lead: bool = False) -> str:
    title = (bot.title or bot.role or "").strip()
    lead_mark = " lead=yes" if lead else ""
    return f"- {bot.name} [{bot.id}] title={title} role={bot.role}{lead_mark}"


def execute_list_members(
    *,
    role: str = "",
    project_id: str = "",
) -> str:
    """Who is on the bound project (or the whole catalog). Index only — use get_member for bios."""
    bound = _bound_project(project_id)
    if isinstance(bound, str):
        return bound
    registry, project = bound
    role_f = str(role or "").strip().lower()
    bots = registry.project_bots(project)
    heading = f"On {project.name} [{project.id}]"
    if not bots:
        bots = list(registry.bots.values())
        heading = "Catalog (none staffed on this project yet)"
    if role_f:
        bots = [b for b in bots if b.role == role_f]
    lines = [f"{heading} — {len(bots)} shown:"]
    if not bots:
        lines.append("  (no matching members)")
    for bot in bots:
        lines.append(_member_index_line(bot, lead=bot.lead))
    lines.append(
        "\nCall get_member bot_id=<id> for bio, skills, and photo. Do not invent people."
    )
    return "\n".join(lines)


def execute_get_member(*, bot_id: str, project_id: str = "") -> str:
    """Full public dossier for one teammate."""
    bid = str(bot_id or "").strip()
    if not bid:
        return "get_member needs bot_id (id or name). Call list_members first."
    bound = _bound_project(project_id)
    if isinstance(bound, str):
        return bound
    registry, project = bound
    bot = _resolve_member(bid, project, registry)
    if bot is None:
        ids = ", ".join(f"{b.name} [{b.id}]" for b in registry.bots.values()) or "(none)"
        return f"Unknown member {bid!r}. Catalog: {ids}."
    return format_member_card(bot, lead=bot.lead)


def _resolve_member(token: str, project: Project, registry: Registry) -> Bot | None:
    needle = (token or "").strip().lower()
    if not needle:
        return None
    for bot in registry.project_bots(project):
        if bot.id.lower() == needle or bot.name.lower() == needle:
            return bot
    for bot in registry.bots.values():
        if bot.id.lower() == needle or bot.name.lower() == needle:
            return bot
        first = bot.name.split()[0].lower() if bot.name else ""
        if first and first == needle:
            return bot
    return None


def execute_list_roster(
    *,
    bot_id: str = "",
    project_id: str = "",
) -> str:
    """All dossiers, or one bot. Prefer list_members -> get_member."""
    bid = str(bot_id or "").strip()
    if bid:
        return execute_get_member(bot_id=bid, project_id=project_id)
    bound = _bound_project(project_id)
    if isinstance(bound, str):
        return bound
    registry, project = bound
    return format_project_roster(project, registry)
