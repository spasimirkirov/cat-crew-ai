"""The harness's own project check, and the SOP handing a broken project back to the cat that broke it."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.harness.project_check import CheckResult, check_project
from backend.platform import sop
from backend.platform.models import Bot, Project, Registry

needs_php = pytest.mark.skipif(shutil.which("php") is None, reason="php is not installed")


def _laravel_like(root: Path, compiled_body: str) -> None:
    (root / "artisan").write_text("<?php exit(0);\n", encoding="utf-8")  # view:cache "succeeds"
    views = root / "resources" / "views"
    views.mkdir(parents=True)
    (views / "home.blade.php").write_text("@if($a) hi", encoding="utf-8")
    compiled = root / "storage" / "framework" / "views"
    compiled.mkdir(parents=True)
    (compiled / "abc.php").write_text(
        compiled_body + f"<?php /**PATH {views / 'home.blade.php'} ENDPATH**/ ?>", encoding="utf-8"
    )
    (root / "app").mkdir()
    (root / "app" / "Ok.php").write_text("<?php\nclass Ok {}\n", encoding="utf-8")


@needs_php
def test_a_blade_view_with_an_unclosed_if_is_reported_against_its_source(tmp_path) -> None:
    _laravel_like(tmp_path, "<?php if ($a): ?> hi ")
    result = check_project(tmp_path)
    assert result is not None and not result.ok
    problem = next(p for p in result.problems if p.startswith("Blade view"))
    assert "home.blade.php" in problem and "unexpected end of file" in problem and "@endif" in problem
    assert "PHP syntax (1 files)" in result.passed


@needs_php
def test_a_healthy_project_passes(tmp_path) -> None:
    _laravel_like(tmp_path, "<?php if ($a): ?> hi <?php endif; ?>")
    result = check_project(tmp_path)
    assert result is not None and result.ok, result.report()


@needs_php
def test_a_php_syntax_error_is_reported(tmp_path) -> None:
    _laravel_like(tmp_path, "")
    (tmp_path / "app" / "Broken.php").write_text("<?php\nclass Broken {\n", encoding="utf-8")
    result = check_project(tmp_path)
    assert any("app" in p and "Broken.php" in p for p in result.problems)


def test_a_folder_with_no_known_stack_is_not_checked(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert check_project(tmp_path) is None


def _bots() -> tuple[Registry, Project]:
    pulse = Bot(id="pulse", name="Pulse", role="developer", system_prompt="Build.")
    project = Project(id="p1", name="P", bot_ids=["pulse"], folders=["D:/site"])
    return Registry(bots={"pulse": pulse}, projects={"p1": project}), project


def test_the_cat_that_broke_the_project_fixes_it_before_its_work_counts(monkeypatch, isolated) -> None:
    registry, project = _bots()
    broken = CheckResult(problems=["Blade view resources/views/home.blade.php: unexpected end of file"])
    checks = iter([broken, CheckResult(passed=["Blade views compile and parse (5 views)"])])
    monkeypatch.setattr(sop, "_project_check", lambda project: next(checks))
    tasks: list[str] = []

    def fake_run(bot, task, **kwargs):
        tasks.append(task)
        kwargs["collect_tool_events"].append(f"{bot.name} executed replace_in_file on home.blade.php")
        return "Done."

    monkeypatch.setattr(sop, "run_delegated", fake_run)
    turn = sop._run_one(registry.bots["pulse"], "add SEO tags", project=project, registry=registry, phase="spontaneous")
    assert len(tasks) == 2 and "it is BROKEN" in tasks[1] and "home.blade.php" in tasks[1]
    assert turn.verified and "Blade views compile and parse" in turn.text


def test_a_project_still_broken_after_the_fixes_is_not_done(monkeypatch, isolated) -> None:
    registry, project = _bots()
    monkeypatch.setattr(sop, "_project_check", lambda project: CheckResult(problems=["GET /: HTTP 500"]))

    def fake_run(bot, task, **kwargs):
        kwargs["collect_tool_events"].append(f"{bot.name} executed write_file on a.php")
        return "Done."

    monkeypatch.setattr(sop, "run_delegated", fake_run)
    turn = sop._run_one(registry.bots["pulse"], "build", project=project, registry=registry, phase="build")
    assert not turn.verified and "still broken" in turn.text and "GET /: HTTP 500" in turn.text
