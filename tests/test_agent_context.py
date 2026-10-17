from pathlib import Path

from jinx_ai.agent_context import (
    expand_slash_command,
    list_skills,
    list_templates,
    load_agents_markdown,
    load_jinx_identity,
    load_workspace_docs,
    looks_like_web_task,
    skills_prompt_index,
)
from jinx_ai.config import BASE_DIR


def test_skills_index_includes_bundled_jinx_self() -> None:
    names = {skill.name.lower() for skill in list_skills()}
    assert "jinx-self" in names
    assert "coding-companion" in names
    assert "static-web" in names
    assert "comfy" in names
    assert "docker" in names
    index = skills_prompt_index()
    assert "jinx-self" in index
    assert "coding-companion" in index
    assert "static-web" in index
    assert "comfy" in index
    assert "docker" in index
    assert "read_skill" in index


def test_prompt_roots_include_package_prompts() -> None:
    paths = {item.path.resolve() for item in list_templates()}
    bundled = (BASE_DIR / "jinx_ai" / "prompts" / "review.md").resolve()
    assert bundled in paths


def test_agents_markdown_empty_without_attached_folder(monkeypatch) -> None:
    monkeypatch.setattr("jinx_ai.agent_context.current_folders", lambda: [])
    text = load_agents_markdown()
    assert text == ""


def test_agents_markdown_reads_attached_folder(tmp_path: Path, monkeypatch) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("Prefer small patches over rewrites.\n", encoding="utf-8")
    monkeypatch.setattr("jinx_ai.agent_context.current_folders", lambda: [tmp_path])
    text = load_agents_markdown()
    assert "Prefer small patches" in text


def test_agents_markdown_includes_jinx_repo_only_when_attached(monkeypatch) -> None:
    monkeypatch.setattr("jinx_ai.agent_context.current_folders", lambda: [BASE_DIR])
    text = load_agents_markdown()
    assert "Prefer small patches" in text


def test_jinx_identity_is_her_own_doc() -> None:
    text = load_jinx_identity()
    assert "AI agents" in text
    assert "AI Assistant" in text
    assert "desktop companion" not in text.lower()
    assert "personal AI companion" not in text.lower()
    assert "generate_image" in text
    assert "This document is loaded into every chat turn" in text


def test_workspace_docs_load_project_readme_not_jinx_identity(tmp_path, monkeypatch) -> None:
    (tmp_path / "README.md").write_text("# Team Arcane landing\nHero plus roster.", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Use Vite. Do not invent copy.", encoding="utf-8")
    monkeypatch.setattr("jinx_ai.agent_context.current_folders", lambda: [tmp_path])
    text = load_workspace_docs()
    assert "Team Arcane landing" in text
    assert "Use Vite" in text
    assert "Windows desktop multi-bot studio" not in text


def test_looks_like_web_task() -> None:
    assert looks_like_web_task("create a cool react website about Jinx")
    assert looks_like_web_task("bootstrap css and fontawesome landing page")
    assert not looks_like_web_task("fix the git commit message")
    assert not looks_like_web_task("react native android build")


def test_expand_ignores_non_slash_paths() -> None:
    body, name = expand_slash_command(str(Path("C:/tmp/file.py")))
    assert name is None
    assert "C:" in body or "tmp" in body
