"""Project instructions (AGENTS.md / SYSTEM.md) and on-demand skills."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import BASE_DIR
from backend.services.workspace import current_folders

_AGENTS_BUDGET = 12_000
_SYSTEM_BUDGET = 8_000
_SKILL_BUDGET = 8_000
_TEMPLATE_BUDGET = 6_000
_IDENTITY_BUDGET = 20_000


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    body: str
    version: str = "1.0.0"
    target_roles: tuple[str, ...] = ()


def _git_top(folder: Path) -> Path:
    try:
        from backend.services.git_ops import git_root

        root = git_root(folder)
        if root is not None:
            return root
    except OSError:
        pass
    return folder


def _walk_named(folder: Path, filename: str) -> list[Path]:
    start = folder.resolve()
    stop = _git_top(start).resolve()
    found: list[Path] = []
    seen: set[Path] = set()
    current = start
    while True:
        candidate = current / filename
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if candidate.is_file() and resolved not in seen:
            found.append(candidate)
            seen.add(resolved)
        if current == stop or current.parent == current:
            break
        current = current.parent
    found.reverse()
    return found


def _context_folders() -> list[Path]:
    """Attached workspace folders only (not the app install / source tree)."""
    out: list[Path] = []
    seen: set[Path] = set()
    for folder in current_folders():
        try:
            resolved = folder.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(folder)
    return out


def _read_capped(path: Path, budget: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = text.strip()
    if len(text) > budget:
        return text[:budget] + "\n... [truncated]"
    return text


def load_catcrew_identity() -> str:
    """Who she is and what she can do — always from her own repo, not the session workspace."""
    path = Path(__file__).with_name("identity.md")
    return _read_capped(path, _IDENTITY_BUDGET)


def load_agents_markdown() -> str:
    chunks: list[str] = []
    used = 0
    for folder in _context_folders():
        for path in _walk_named(folder, "AGENTS.md"):
            body = _read_capped(path, _AGENTS_BUDGET - used)
            if not body:
                continue
            chunks.append(f"### {path}\n{body}")
            used += len(body)
            if used >= _AGENTS_BUDGET:
                return "\n\n".join(chunks)
    return "\n\n".join(chunks)


def load_system_markdown() -> str:
    chunks: list[str] = []
    used = 0
    for folder in _context_folders():
        for path in _walk_named(folder, "SYSTEM.md"):
            body = _read_capped(path, _SYSTEM_BUDGET - used)
            if not body:
                continue
            chunks.append(body)
            used += len(body)
            if used >= _SYSTEM_BUDGET:
                return "\n\n".join(chunks)
    return "\n\n".join(chunks)


def load_workspace_docs() -> str:
    """AGENTS.md, SYSTEM.md, and README from attached project folders — not CatCrewAI source."""
    chunks: list[str] = []
    agents = load_agents_markdown()
    system = load_system_markdown()
    if agents:
        chunks.append("Project AGENTS.md:\n" + agents)
    if system:
        chunks.append("Project SYSTEM.md:\n" + system)
    used = 0
    budget = 6_000
    for folder in _context_folders():
        readme: Path | None = None
        for name in ("README.md", "readme.md", "README.txt"):
            candidate = folder / name
            if candidate.is_file():
                readme = candidate
                break
        if readme is None:
            continue
        body = _read_capped(readme, budget - used)
        if not body:
            continue
        chunks.append(f"Project {readme.name} ({readme}):\n{body}")
        used += len(body)
        if used >= budget:
            break
    return "\n\n".join(chunks)


def _pack_roots(kind: str) -> list[Path]:
    """Bundled playbooks, optional C:\\CatCrewAI assets, and existing project overlay."""
    from .config import CATCREW_HOME

    roots = [
        BASE_DIR / "backend" / kind,
        CATCREW_HOME / kind,
    ]
    for folder in current_folders():
        roots.append(folder / ".cat-crew-ai" / kind)
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved in seen or not root.is_dir():
            continue
        seen.add(resolved)
        unique.append(root)
    return unique


def _skill_roots() -> list[Path]:
    return _pack_roots("skills")


def _parse_frontmatter(block: str) -> dict[str, str]:
    """Parse a small subset of YAML: plain ``key: value`` lines plus block
    scalars (``key: >-`` / ``>`` / ``|-`` / ``|`` followed by indented
    continuation lines). Good enough for our own SKILL.md frontmatter —
    not a general YAML parser.
    """
    meta: dict[str, str] = {}
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or ":" not in line or line[:1] in (" ", "\t"):
            i += 1
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if value in (">-", ">", ">+", "|-", "|", "|+"):
            folded = value.startswith(">")
            collected: list[str] = []
            i += 1
            while i < len(lines) and (lines[i][:1] in (" ", "\t") or not lines[i].strip()):
                collected.append(lines[i].strip())
                i += 1
            while collected and not collected[-1]:
                collected.pop()
            text = " ".join(part for part in collected if part) if folded else "\n".join(collected)
            meta[key] = text.strip()
            continue
        meta[key] = value.strip('"').strip("'")
        i += 1
    return meta


def _parse_skill(path: Path, fallback: str) -> Skill | None:
    raw = _read_capped(path, _SKILL_BUDGET + 400)
    if not raw:
        return None
    meta: dict[str, str] = {}
    body = raw
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end > 0:
            block = raw[3:end].strip()
            body = raw[end + 4 :].lstrip("\n")
            meta = _parse_frontmatter(block)
    name = (meta.get("name") or fallback).strip() or fallback
    description = (meta.get("description") or "").strip()
    if not description:
        for line in body.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                description = line[:160]
                break
    if not description:
        description = name
    version = (meta.get("version") or "").strip() or "1.0.0"
    target_roles = tuple(
        role.strip().lower()
        for role in (meta.get("target_roles") or "").split(",")
        if role.strip()
    )
    if len(body) > _SKILL_BUDGET:
        body = body[:_SKILL_BUDGET] + "\n... [truncated]"
    return Skill(
        name=name,
        description=description,
        path=path,
        body=body,
        version=version,
        target_roles=target_roles,
    )


def list_skills() -> list[Skill]:
    skills: list[Skill] = []
    names: set[str] = set()
    for root in _skill_roots():
        try:
            children = sorted(root.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for child in children:
            path: Path | None = None
            fallback = child.stem
            if child.is_dir():
                candidate = child / "SKILL.md"
                if candidate.is_file():
                    path = candidate
                    fallback = child.name
            elif child.suffix.lower() == ".md" and child.name.upper() != "README.MD":
                path = child
            if path is None:
                continue
            skill = _parse_skill(path, fallback)
            if skill is None:
                continue
            key = skill.name.lower()
            if key in names:
                continue
            names.add(key)
            skills.append(skill)
    return skills


def find_skill(name: str) -> Skill | None:
    needle = (name or "").strip().lower()
    if not needle:
        return None
    for skill in list_skills():
        if skill.name.lower() == needle:
            return skill
    return None


def _resolve_agent_id(agent_id: str) -> str:
    agent_id = str(agent_id or "").strip()
    if agent_id:
        return agent_id
    try:
        from .platform.runtime import current_bot_id

        return current_bot_id()
    except Exception:  # noqa: BLE001 — runtime ContextVar is best-effort here
        return ""


def _agent_assigned_skill_names(agent_id: str) -> set[str] | None:
    """Lowercased names of skills this agent may see, or ``None`` when the
    DB skill catalog has never been synced (fresh checkout — stay
    unfiltered rather than going silently skill-blind before
    ``scripts/init_catcrew_team.py`` has run once)."""
    from backend.db import ledger as db

    try:
        catalog = db.list_skills()
    except Exception:  # noqa: BLE001
        catalog = []
    if not catalog:
        return None
    try:
        assigned = db.skills_for_agent(agent_id)
    except Exception:  # noqa: BLE001
        assigned = []
    names = {str(item.get("name") or "").strip().lower() for item in assigned}
    return names | set(_bundle_skills(agent_id))


def _bundle_skills(agent_id: str) -> tuple[str, ...]:
    """Playbooks ``agent_id`` always has (``platform/essentials.py``): its cat bundle, or Cat
    Crew's own set for the orchestrator, which has no ``agent_skills`` rows."""
    from backend.platform.essentials import CREW_SKILLS, essential_skills_for_agent
    from backend.platform.orchestrator import is_room_id

    return CREW_SKILLS if is_room_id(agent_id) else essential_skills_for_agent(agent_id)


def read_skill(name: str, *, agent_id: str = "") -> str:
    skill = find_skill(name)
    if skill is None:
        available = ", ".join(item.name for item in list_skills()) or "(none)"
        return f"Unknown skill {name!r}. Available: {available}"
    resolved_agent = _resolve_agent_id(agent_id)
    if resolved_agent:
        allowed = _agent_assigned_skill_names(resolved_agent)
        if allowed is not None and skill.name.strip().lower() not in allowed:
            return (
                f"Skill {skill.name!r} exists but is not assigned to you. "
                "Ask to have it assigned in Settings -> Skills."
            )
    return f"# {skill.name}\n{skill.description}\n\n{skill.body}"


def slugify_skill_name(name: str) -> str:
    """Turn a display name into a filesystem/slug-safe identifier — lowercase,
    hyphen-separated, no repeats. Used for the "+ Add Skill" modal so a typed
    name like "Stripe Integration" becomes ``stripe-integration``."""
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug


def custom_skills_dir() -> Path:
    """Where a user-authored skill (Settings → Skills → + Add Skill) is
    written — the optional ``C:\\CatCrewAI\\skills`` overlay, never the bundled
    ``backend/skills`` tree (that's CatCrewAI's own source, not user data)."""
    from .config import CATCREW_HOME

    return CATCREW_HOME / "skills"


def create_custom_skill(name: str, description: str, body: str) -> Skill:
    """Write a new single-file SKILL.md-style playbook to the custom skills
    overlay and return the parsed ``Skill``. Raises ``ValueError`` if the
    name is empty or a skill with the same slug already exists (on disk,
    anywhere in ``_skill_roots()`` — bundled or custom)."""
    display_name = (name or "").strip()
    if not display_name:
        raise ValueError("Name is required.")
    slug = slugify_skill_name(display_name)
    if not slug:
        raise ValueError("Name must contain at least one letter or number.")
    if find_skill(slug) is not None or find_skill(display_name) is not None:
        raise ValueError(f"A skill named {display_name!r} already exists.")
    one_line_description = " ".join((description or "").split()).strip()
    target_dir = custom_skills_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{slug}.md"
    raw_body = (body or "").strip()
    if raw_body.startswith("---"):
        # Already a full SKILL.md (own frontmatter) — e.g. uploaded rather
        # than pasted as plain text. Keep it verbatim; its own name/
        # description win over the modal fields once parsed.
        content = raw_body + "\n"
    else:
        frontmatter_desc = one_line_description or display_name
        content = (
            f"---\nname: {slug}\ndescription: {frontmatter_desc}\n---\n\n"
            f"# {display_name}\n\n{raw_body}\n"
        )
    path.write_text(content, encoding="utf-8")
    skill = _parse_skill(path, slug)
    if skill is None:  # pragma: no cover — we just wrote valid content
        raise ValueError("Could not read back the new skill file.")
    return skill


def delete_custom_skill(path: Path) -> bool:
    """Remove a user-authored skill file. Refuses (returns False) for
    anything outside the custom skills overlay — bundled skills under
    ``backend/skills`` are source, not user data, and are never deleted by
    the app."""
    try:
        resolved = path.resolve()
        custom_root = custom_skills_dir().resolve()
        if not resolved.is_relative_to(custom_root):
            return False
    except (OSError, ValueError):
        return False
    try:
        resolved.unlink()
        return True
    except OSError:
        return False


def is_bundled_skill(path: Path) -> bool:
    """True if ``path`` lives under the repo's own ``backend/skills`` tree —
    CatCrewAI's shipped playbooks, never deletable from the UI."""
    try:
        return path.resolve().is_relative_to((BASE_DIR / "backend" / "skills").resolve())
    except (OSError, ValueError):
        return False


# --- GitHub ingestion (Phase 15 §2c / Task #25) -----------------------------
#
# Shared network/git primitives used by both skill ingestion (below) and
# ``platform/tool_registry.py``'s ``import_tool_from_url`` — a shallow clone
# into a scratch dir is simpler and more robust than a GitHub API client for
# arbitrary repo layouts, matching how this codebase already shells out to
# git elsewhere (``git_ops.py``) rather than adding a dependency. This is a
# fresh scratch clone of an *external* URL, never the workspace's own repo —
# for that, see ``git_ops.py``.

_GITHUB_BLOB_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$")
_GITHUB_TREE_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.+)$")
_GITHUB_REPO_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")


def fetch_raw_github_file(url: str) -> str:
    """Fetch one text file over HTTPS. Rewrites a ``github.com/.../blob/...``
    URL to its ``raw.githubusercontent.com`` form first; a URL that is
    already raw (or any other plain HTTPS text URL) is fetched as-is."""
    target = url
    match = _GITHUB_BLOB_RE.match(url)
    if match:
        owner, repo, ref, path = match.groups()
        target = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
    req = urllib.request.Request(target, headers={"User-Agent": "CatCrewAI"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 — https only, fixed timeout
        return resp.read().decode("utf-8", errors="replace")


def shallow_git_clone(url: str, dest: Path, *, ref: str = "") -> str | None:
    """Shallow-clone ``url`` into ``dest``. Returns ``None`` on success, or
    a short error string — never raises, matching every other ingestion
    helper's convention in this module."""
    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    cmd += [url, str(dest)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return f"git clone of {url} timed out."
    except OSError as exc:
        return f"Could not run git: {exc}"
    if result.returncode != 0:
        return f"git clone failed: {(result.stderr or result.stdout or '').strip()[:500]}"
    return None


def import_skill_from_url(url: str) -> str:
    """Import one or more SKILL.md playbooks from a GitHub URL: a raw file
    URL, a ``github.com/.../blob/....md`` single-file URL, or a
    ``github.com/owner/repo`` (optionally ``/tree/<branch>/<subfolder>``)
    URL — cloned shallowly and scanned for every ``SKILL.md`` under the
    given subfolder (or the whole repo), falling back to every ``*.md`` if
    none are named that.

    Each found file is parsed with the same ``_parse_skill`` used for
    on-disk skills, written verbatim into ``custom_skills_dir()`` (an
    existing file of the same slug is overwritten — re-importing the same
    URL is a refresh, not a duplicate), and upserted into the catalog
    tagged ``type='marketplace'``, ``source_url=url``. A multi-skill repo
    does not fail atomically: each file's outcome is reported on its own
    line so one bad file doesn't hide the rest.
    """
    from backend.db import ledger as db

    target = (url or "").strip()
    if not target:
        return "Error: empty url."
    if not target.startswith(("http://", "https://")):
        return "Error: expected an http(s) GitHub URL."

    found_files: list[Path] = []
    tmp_dir = tempfile.TemporaryDirectory(prefix="catcrew_skill_import_")
    try:
        if target.lower().endswith(".md"):
            try:
                text = fetch_raw_github_file(target)
            except Exception as exc:  # noqa: BLE001
                return f"Error: could not fetch {target}: {exc}"
            scratch = Path(tmp_dir.name) / "SKILL.md"
            scratch.write_text(text, encoding="utf-8")
            found_files.append(scratch)
        else:
            match = _GITHUB_TREE_RE.match(target)
            if match:
                owner, repo, ref, subfolder = match.groups()
            else:
                match = _GITHUB_REPO_RE.match(target)
                if not match:
                    return (
                        "Error: unrecognized GitHub URL. Use a raw .md file, a "
                        "github.com/.../blob/....md file, or a github.com/owner/repo "
                        "(optionally /tree/<branch>/<subfolder>) URL."
                    )
                owner, repo = match.groups()
                ref, subfolder = "", ""
            clone_dest = Path(tmp_dir.name) / "repo"
            err = shallow_git_clone(f"https://github.com/{owner}/{repo}.git", clone_dest, ref=ref)
            if err:
                return f"Error: {err}"
            scan_root = clone_dest / subfolder if subfolder else clone_dest
            if not scan_root.is_dir():
                return f"Error: {subfolder!r} not found in {owner}/{repo}."
            named = sorted(scan_root.rglob("SKILL.md"))
            found_files = named if named else sorted(scan_root.glob("*.md"))
            if not found_files:
                return f"Error: no SKILL.md or .md files found under {target}."

        results: list[str] = []
        for path in found_files:
            fallback = path.parent.name if path.name.upper() == "SKILL.MD" else path.stem
            skill = _parse_skill(path, fallback)
            if skill is None:
                results.append(f"  {path.name}: could not parse (empty or unreadable).")
                continue
            slug = slugify_skill_name(skill.name)
            if not slug:
                results.append(f"  {path.name}: skipped (no usable name).")
                continue
            target_dir = custom_skills_dir()
            target_dir.mkdir(parents=True, exist_ok=True)
            out_path = target_dir / f"{slug}.md"
            try:
                shutil.copyfile(path, out_path)
            except OSError as exc:
                results.append(f"  {skill.name}: could not write to disk: {exc}")
                continue
            db.upsert_skill(
                slug=skill.name,
                name=skill.name,
                description=skill.description,
                playbook_path=str(out_path),
                type="marketplace",
                version=skill.version,
                target_roles=",".join(skill.target_roles),
                source_url=target,
            )
            results.append(f"  {skill.name}: imported -> {out_path}")
        if not results:
            return f"Error: nothing importable found at {target}."
        return f"Imported from {target}:\n" + "\n".join(results)
    finally:
        tmp_dir.cleanup()


def skills_prompt_index(agent_id: str = "") -> str:
    """Name + description only (full body loads lazily via ``read_skill``).

    Filtered to what ``agent_id`` can actually see — explicitly assigned
    skills plus every ``is_global`` skill (CatCrewAI Dynamic Skill
    Management), via the same ``agent_skills`` catalog the Skills Matrix UI
    edits — instead of every playbook on disk. ``agent_id`` defaults to the
    current turn's bot via ``platform.runtime.current_bot_id()`` when not
    given explicitly, so existing no-arg call sites are filtered too. Falls
    back to the old unfiltered "every skill on disk" index when there is
    genuinely no agent in scope, or when the DB catalog has never been
    synced (a fresh checkout isn't silently skill-blind before
    ``scripts/init_catcrew_team.py`` runs)."""
    resolved_agent = _resolve_agent_id(agent_id)
    if resolved_agent:
        allowed = _agent_assigned_skill_names(resolved_agent)
        if allowed is not None:
            from backend.db import ledger as db

            try:
                assigned = db.skills_for_agent(resolved_agent)
            except Exception:  # noqa: BLE001
                assigned = []
            # Bundled playbooks are listed even when their DB row is missing (platform/essentials.py).
            have = {str(item.get("slug") or item.get("name") or "").strip().lower() for item in assigned}
            for slug in _bundle_skills(resolved_agent):
                if slug not in have:
                    skill = find_skill(slug)
                    if skill is not None:
                        assigned.append({"name": skill.name, "description": skill.description})
            if not assigned:
                return ""
            lines = [
                "Skills (call read_skill with the name when you need the full text; "
                "do not guess the contents):"
            ]
            for skill in assigned:
                lines.append(f"- {skill['name']}: {skill['description']}")
            return "\n".join(lines)
    skills = list_skills()
    if not skills:
        return ""
    lines = [
        "Skills (call read_skill with the name when you need the full text; "
        "do not guess the contents):"
    ]
    for skill in skills:
        lines.append(f"- {skill.name}: {skill.description}")
    return "\n".join(lines)


_WEB_HINTS = (
    "website",
    "landing",
    "landing page",
    "html",
    "bootstrap",
    "fontawesome",
    "font awesome",
    "web page",
    "webpage",
    "front-end",
    "frontend",
    "css",
)


def looks_like_web_task(*chunks: str) -> bool:
    """True when this turn should get the static-web skill without waiting for read_skill."""
    text = " ".join(chunks).lower()
    if not text.strip():
        return False
    if "react native" in text:
        return False
    return any(hint in text for hint in _WEB_HINTS) or "react" in text


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    description: str
    path: Path
    body: str


def list_templates() -> list[PromptTemplate]:
    templates: list[PromptTemplate] = []
    names: set[str] = set()
    for root in _pack_roots("prompts"):
        try:
            children = sorted(root.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for child in children:
            if not child.is_file() or child.suffix.lower() != ".md":
                continue
            if child.name.upper() == "README.MD":
                continue
            parsed = _parse_skill(child, child.stem)
            if parsed is None:
                continue
            key = parsed.name.lower()
            if key in names:
                continue
            names.add(key)
            templates.append(
                PromptTemplate(
                    name=parsed.name,
                    description=parsed.description,
                    path=parsed.path,
                    body=parsed.body[:_TEMPLATE_BUDGET],
                )
            )
    return templates


def expand_slash_command(text: str) -> tuple[str, str | None]:
    """Expand `/name extra` using a prompt template. Unknown slashes stay as-is."""
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return raw, None
    rest = raw[1:]
    name, _, extra = rest.partition(" ")
    name = name.strip().lstrip("/")
    extra = extra.strip()
    if not name or "/" in name or "\\" in name:
        return raw, None
    needle = name.lower()
    for template in list_templates():
        if template.name.lower() == needle:
            if extra:
                return f"{template.body.strip()}\n\n{extra}", template.name
            return template.body.strip(), template.name
    return raw, None


def templates_prompt_index() -> str:
    templates = list_templates()
    if not templates:
        return ""
    lines = ["Slash templates (type /name in chat to expand):"]
    for item in templates:
        lines.append(f"- /{item.name}: {item.description}")
    return "\n".join(lines)
