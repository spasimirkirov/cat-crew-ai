"""docs_search: official docs only, aware of the version the open project runs; every building cat has it."""

from __future__ import annotations

import json

import pytest

from backend import tools


@pytest.fixture
def searched(monkeypatch):
    queries: list[str] = []

    def fake(query: str) -> str:
        queries.append(query)
        return "1. Result\nhttps://example.test\nsnippet"

    monkeypatch.setattr(tools, "web_search", fake)
    return queries


def test_a_named_source_searches_only_that_site(searched, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(tools, "current_workspace", lambda: tmp_path)
    out = tools.docs_search("utility classes for grid", "tailwind")
    assert searched == ["utility classes for grid site:tailwindcss.com/docs"]
    assert "web_fetch" in out


def test_the_source_is_guessed_and_the_laravel_version_is_read_from_the_project(searched, monkeypatch, tmp_path) -> None:
    (tmp_path / "composer.lock").write_text(
        json.dumps({"packages": [{"name": "laravel/framework", "version": "v13.34.0"}]}), encoding="utf-8"
    )
    monkeypatch.setattr(tools, "current_workspace", lambda: tmp_path)
    out = tools.docs_search("artisan storage:link public disk")
    assert searched[0].endswith("site:laravel.com")
    assert "Laravel 13.34.0" in out and "https://laravel.com/docs/13.x" in out


def test_an_unknown_topic_falls_back_to_a_web_search(searched) -> None:
    out = tools.docs_search("quaternion slerp")
    assert searched == ["quaternion slerp official documentation"] and "name the source" in out


def test_every_cat_that_builds_or_checks_can_look_docs_up() -> None:
    from backend.platform.toolset import builtin_names_for_role

    for role in ("vanguard", "devops", "developer", "designer", "qa"):
        names = builtin_names_for_role(role, ["read_file"])  # even a trimmed tool list keeps them
        assert {"docs_search", "web_search", "web_fetch"} <= set(names), role


def test_the_new_playbooks_exist_and_are_essential() -> None:
    from backend.agent_context import find_skill
    from backend.platform.essentials import essential_skills_for_agent

    for slug in ("docs-lookup", "laravel", "design-md"):
        assert find_skill(slug) is not None, slug
    assert {"docs-lookup", "laravel", "design-md"} <= set(essential_skills_for_agent("pulse"))
    assert {"docs-lookup", "laravel"} <= set(essential_skills_for_agent("forge"))


def test_context7_and_chrome_devtools_are_configured_for_the_builders() -> None:
    from backend.services.mcp_service import CONFIG_PATH

    servers = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["servers"]
    assert servers["context7"]["transport"] == "streamable-http"
    assert {"forge", "pulse"} <= set(servers["context7"]["bind_to"])
    assert set(servers["chrome_devtools"]["bind_to"]) == {"aegis", "pixel"}
