"""Project instructions (AGENTS.md / SYSTEM.md) and on-demand skills."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import BASE_DIR
from .workspace import current_folders

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


def _git_top(folder: Path) -> Path:
    try:
        from .git_ops import git_root

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


def load_jinx_identity() -> str:
    """Who she is and what she can do — always from her own repo, not the session workspace."""
    path = BASE_DIR / "docs" / "JINX.md"
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
    """AGENTS.md, SYSTEM.md, and README from attached project folders — not Jinx source."""
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
    """Bundled playbooks, optional C:\\Jinx-AI assets, and existing project overlay."""
    from .config import JINX_HOME

    roots = [
        BASE_DIR / "jinx_ai" / kind,
        JINX_HOME / kind,
    ]
    for folder in current_folders():
        roots.append(folder / ".jinx-ai" / kind)
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
            for line in block.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                meta[key.strip().lower()] = value.strip().strip('"').strip("'")
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
    if len(body) > _SKILL_BUDGET:
        body = body[:_SKILL_BUDGET] + "\n... [truncated]"
    return Skill(name=name, description=description, path=path, body=body)


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


def read_skill(name: str) -> str:
    skill = find_skill(name)
    if skill is None:
        available = ", ".join(item.name for item in list_skills()) or "(none)"
        return f"Unknown skill {name!r}. Available: {available}"
    return f"# {skill.name}\n{skill.description}\n\n{skill.body}"


def skills_prompt_index() -> str:
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
