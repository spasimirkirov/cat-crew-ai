"""Look up teammates: names, roles, bios, photos. A tool result — not prompt stuffing."""

from __future__ import annotations

from pathlib import Path

from .models import Bot, Project, Registry, Team


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
    lead_mark = " — team lead" if lead else ""
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


def _is_lead(bot: Bot, project: Project, registry: Registry) -> bool:
    for team in registry.assigned_teams(project):
        if (team.lead_bot_id or "").strip() == bot.id:
            return True
    return False


def _team_block(team: Team, registry: Registry) -> list[str]:
    lead_id = (team.lead_bot_id or "").strip()
    lines = [f"Team {team.name} [{team.id}]: {team.description or '(no blurb)'}".rstrip()]
    members = registry.bot_members(team)
    if not members:
        lines.append("  (no members)")
        return lines
    for bot in members:
        lines.append(format_member_card(bot, lead=bot.id == lead_id))
    return lines


def format_project_roster(project: Project, registry: Registry) -> str:
    lines = [
        "Official team roster — names, titles, roles, bios, photos.",
        "Call teammates by name. Use these cards for a team/about page.",
    ]
    teams = registry.assigned_teams(project)
    seen: set[str] = set()
    if not teams and not project.bot_ids:
        lines.append("(no teams or bots assigned yet)")
        return "\n".join(lines)
    for team in teams:
        lines.extend(_team_block(team, registry))
        seen.update(bot.id for bot in registry.bot_members(team))
    standalone = [
        registry.bots[bid]
        for bid in project.bot_ids
        if bid in registry.bots and bid not in seen
    ]
    if standalone:
        lines.append("Standalone on this project:")
        for bot in standalone:
            lines.append(format_member_card(bot, lead=_is_lead(bot, project, registry)))
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


def _resolve_team(token: str, project: Project, registry: Registry) -> Team | None:
    needle = (token or "").strip().lower()
    if not needle:
        return None
    for team in registry.assigned_teams(project):
        if team.id.lower() == needle or team.name.lower() == needle:
            return team
    return None


def _member_index_line(bot: Bot, *, lead: bool = False) -> str:
    title = (bot.title or bot.role or "").strip()
    lead_mark = " lead=yes" if lead else ""
    return (
        f"- {bot.name} [{bot.id}] title={title} role={bot.role}{lead_mark}"
    )


def execute_list_teams(*, project_id: str = "") -> str:
    """Teams on the bound project — ids, leads, counts. Not dossiers."""
    bound = _bound_project(project_id)
    if isinstance(bound, str):
        return bound
    registry, project = bound
    teams = registry.assigned_teams(project)
    lines = [f"Teams on {project.name} [{project.id}]:"]
    if not teams:
        lines.append("(none assigned)")
        if project.bot_ids:
            lines.append(
                f"Standalone bots: {len(project.bot_ids)} — call list_members (no team_id)."
            )
        lines.append("Call list_members with team_id, then get_member with bot_id for a dossier.")
        return "\n".join(lines)
    for team in teams:
        lead = ""
        if team.lead_bot_id and team.lead_bot_id in registry.bots:
            person = registry.bots[team.lead_bot_id]
            lead = f" — lead {person.name} [{person.id}]"
        n = len(registry.bot_members(team))
        lines.append(
            f"- {team.name} [{team.id}]{lead} — {n} member{'s' if n != 1 else ''}"
        )
        if team.description:
            lines.append(f"  {team.description}")
    lines.append("Next: list_members team_id=<id> for the lineup, get_member bot_id=<id> for a dossier.")
    return "\n".join(lines)


def execute_list_members(
    *,
    team_id: str = "",
    role: str = "",
    project_id: str = "",
) -> str:
    """Who is on a team (or the whole project). Index only — use get_member for bios."""
    bound = _bound_project(project_id)
    if isinstance(bound, str):
        return bound
    registry, project = bound
    tid = str(team_id or "").strip()
    role_f = str(role or "").strip().lower()
    blocks: list[str] = []
    if tid:
        team = _resolve_team(tid, project, registry)
        if team is None:
            known = ", ".join(t.id for t in registry.assigned_teams(project)) or "(none)"
            return f"Unknown team {tid!r} on this project. Assigned: {known}."
        teams = [team]
    else:
        teams = registry.assigned_teams(project)
    seen: set[str] = set()
    for team in teams:
        lead_id = (team.lead_bot_id or "").strip()
        members = registry.bot_members(team)
        if role_f:
            members = [b for b in members if b.role == role_f]
        lines = [f"Team {team.name} [{team.id}] — {len(members)} shown:"]
        if not members:
            lines.append("  (no matching members)")
        for bot in members:
            seen.add(bot.id)
            lines.append(_member_index_line(bot, lead=bot.id == lead_id))
        blocks.append("\n".join(lines))
    if not tid:
        standalone = [
            registry.bots[bid]
            for bid in project.bot_ids
            if bid in registry.bots and bid not in seen
        ]
        if role_f:
            standalone = [b for b in standalone if b.role == role_f]
        if standalone:
            lines = [f"Standalone on {project.name}:"]
            for bot in standalone:
                lines.append(_member_index_line(bot))
            blocks.append("\n".join(lines))
    if not blocks:
        return "No members on this project."
    return (
        "\n\n".join(blocks)
        + "\n\nCall get_member bot_id=<id> for bio, skills, and photo. "
        "Do not invent people."
    )


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
        ids = ", ".join(b.id for b in registry.project_bots(project)) or "(none)"
        return f"Unknown member {bid!r} on this project. Known ids: {ids}."
    return format_member_card(bot, lead=_is_lead(bot, project, registry))


def _resolve_member(token: str, project: Project, registry: Registry) -> Bot | None:
    needle = (token or "").strip().lower()
    if not needle:
        return None
    for bot in registry.project_bots(project):
        if bot.id.lower() == needle or bot.name.lower() == needle:
            return bot
    return None


def execute_list_roster(
    *,
    bot_id: str = "",
    team_id: str = "",
    project_id: str = "",
) -> str:
    """All dossiers, or one bot / one team. Prefer list_teams → list_members → get_member."""
    bid = str(bot_id or "").strip()
    tid = str(team_id or "").strip()
    if bid:
        return execute_get_member(bot_id=bid, project_id=project_id)
    bound = _bound_project(project_id)
    if isinstance(bound, str):
        return bound
    registry, project = bound
    if tid:
        team = _resolve_team(tid, project, registry)
        if team is None:
            known = ", ".join(t.id for t in registry.assigned_teams(project)) or "(none)"
            return f"Unknown team {tid!r} on this project. Assigned: {known}."
        return "\n".join(_team_block(team, registry))
    return format_project_roster(project, registry)
