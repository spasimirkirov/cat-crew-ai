"""services/scaffold.py: a framework generator run into a folder that already has files."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from backend.services.scaffold import _with_dir_placeholder, scaffold_project

# Behaves like `composer create-project`: refuses a folder that exists and is not empty.
_GENERATOR = """
import sys
from pathlib import Path
target = Path(sys.argv[1])
if target.exists() and any(target.iterdir()):
    sys.exit('Project directory "%s" is not empty.' % target)
for rel, body in {
    "artisan": "#!/usr/bin/env php",
    "composer.json": '{"name": "laravel/laravel"}',
    "routes/web.php": "<?php",
    "app/Models/User.php": "<?php class User {}",
    "vendor/autoload.php": "<?php",
    "README.md": "Laravel's own readme",
}.items():
    path = target / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
print("Application ready!")
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    ws = tmp_path / "site"
    ws.mkdir()
    (ws / "BLUEPRINT.md").write_text("our plan")
    (ws / "README.md").write_text("our readme")
    (ws / "composer.json").write_text('{"name": "hand-written"}')
    (ws / "app" / "Http" / "Controllers").mkdir(parents=True)
    (ws / "vendor").mkdir()
    (ws / "vendor" / "stale.txt").write_text("from a hand-written composer.json")
    gen = tmp_path / "gen.py"
    gen.write_text(_GENERATOR)
    monkeypatch.setattr("backend.tools.current_workspace", lambda: ws)
    monkeypatch.setattr("backend.tools.current_folders", lambda: [ws])
    monkeypatch.setattr("backend.tools._allowed_roots", lambda: [ws.resolve()])
    return ws, f'"{sys.executable}" "{gen}"'


def test_a_generator_fills_a_folder_that_already_has_files(project) -> None:
    ws, gen = project
    out = scaffold_project(f"{gen} {{dir}}")
    assert out.startswith("Scaffolded"), out
    assert (ws / "artisan").is_file() and (ws / "routes" / "web.php").is_file()
    assert (ws / "app" / "Models" / "User.php").is_file() and (ws / "app" / "Http" / "Controllers").is_dir()
    # The generator owns the manifest and the dependency folder ...
    assert "laravel/laravel" in (ws / "composer.json").read_text()
    assert (ws / "vendor" / "autoload.php").is_file() and not (ws / "vendor" / "stale.txt").exists()
    # ... and every other file the crew wrote stays.
    assert (ws / "BLUEPRINT.md").read_text() == "our plan" and (ws / "README.md").read_text() == "our readme"
    assert "README.md" in out and "composer.json" in out
    assert not any(p.name.startswith(".scaffold-") for p in ws.iterdir())  # staging cleaned up


def test_the_trailing_dot_models_write_becomes_the_target(project) -> None:
    ws, gen = project
    assert scaffold_project(f"{gen} .").startswith("Scaffolded")
    assert (ws / "artisan").is_file()
    assert _with_dir_placeholder("composer create-project --prefer-dist laravel/laravel .") == (
        "composer create-project --prefer-dist laravel/laravel {dir}"
    )
    assert _with_dir_placeholder("npm create vite@latest . -- --template react") == "npm create vite@latest {dir} -- --template react"


def test_a_command_without_a_target_folder_is_explained(project) -> None:
    _ws, gen = project
    assert "{dir}" in scaffold_project(gen)


def test_a_generator_that_makes_nothing_is_an_error_and_leaves_no_trace(project) -> None:
    ws, _gen = project
    out = scaffold_project(f'"{sys.executable}" -c "print(1)" {{dir}}')
    assert out.startswith("Error: the generator created nothing")
    assert not any(p.name.startswith(".scaffold-") for p in ws.iterdir())


def test_run_command_points_a_generator_at_scaffold_project(project) -> None:
    from backend.tools import run_command

    out = run_command("composer create-project --prefer-dist laravel/laravel .")
    assert out.startswith("Refused:") and "Do not delete anything" in out
    assert "scaffold_project(command='composer create-project --prefer-dist laravel/laravel {dir}')" in out


def test_developer_and_devops_have_scaffold_project() -> None:
    from backend.platform.toolset import builtin_names_for_role

    assert "scaffold_project" in builtin_names_for_role("devops")
    assert "scaffold_project" in builtin_names_for_role("developer")


def test_the_crew_is_no_longer_told_to_hand_write_composer_json(isolated, tmp_path) -> None:
    """The local-model Laravel failure: our own DevOps prompt said "write composer.json yourself"."""
    from backend.platform.models import Bot, Project, Registry
    from backend.platform.runner import run_bot
    from backend.providers.base import AssistantMessage, BaseProvider

    seen: list[str] = []

    class _Look(BaseProvider):
        def chat(self, messages, tools=None):
            seen.append(str(messages[0]["content"]))
            return AssistantMessage(content="looked")

        def tool_message(self, tool_call, result):
            return {"role": "tool", "content": result}

    folder = tmp_path / "site"
    folder.mkdir()
    bot = Bot(id="forge", name="Forge", role="devops", system_prompt="DevOps.")
    project = Project(id="p1", name="P", folders=[str(folder)])
    run_bot(bot, "set it up", project=project, registry=Registry(bots={"forge": bot}, projects={"p1": project}), provider=_Look(), auto_build_bridge=False)
    prompt = seen[0]
    assert "write composer.json yourself" not in prompt.lower()
    assert "scaffold_project" in prompt and "composer create-project laravel/laravel {dir}" in prompt
