"""Role tools are registered at start-up and every cat always gets its whole bundle."""

from __future__ import annotations

import pytest

from backend.db import ledger as db
from backend.platform import role_tools
from backend.platform.essentials import BROWSER_ALL, BROWSER_READ, ESSENTIAL_ROLE_TOOLS

BROWSER = [slug for slug, _ in role_tools._BROWSER_TOOLS]


def _slugs(agent: str) -> set[str]:
    return {t["slug"] for t in db.tools_for_agent(agent)}


@pytest.fixture
def home(isolated):
    db.init_db()
    return isolated


def test_fresh_install_gives_every_cat_its_bundle(home):
    role_tools.ensure_role_tools()
    for bot_id, slug in ESSENTIAL_ROLE_TOOLS:
        assert slug in _slugs(bot_id), (bot_id, slug)
    assert set(BROWSER) <= _slugs("aegis")
    assert set(BROWSER) <= _slugs("pixel")
    assert {"http_endpoint_audit", "run_test_suite"} <= _slugs("aegis")
    assert "seo_page_audit" in _slugs("blaze") and "data_collection_scan" in _slugs("lex")
    assert "browser_click" not in _slugs("pulse")
    assert "browser_click" not in _slugs("lex")  # Lex only reads pages
    assert db.get_meta("role_tools_version") == str(role_tools.ROLE_TOOLS_VERSION)
    assert not (home / "catcrew_home").exists()  # no stray files outside the database


def test_a_removed_bundle_tool_comes_back_on_the_next_start(home):
    role_tools.ensure_role_tools()
    db.unassign_tool("pixel", db.get_tool_by_slug("browser_click")["id"])
    db.unassign_tool("forge", db.get_tool_by_slug("vps_health_check")["id"])
    role_tools.ensure_role_tools()
    assert "browser_click" in _slugs("pixel")
    assert "vps_health_check" in _slugs("forge")


def test_role_tools_are_exactly_the_bundles():
    assert {(bot, slug) for bot, slug, _ in role_tools.ROLE_TOOLS} == set(ESSENTIAL_ROLE_TOOLS)
    bundled = {slug for _, slug in ESSENTIAL_ROLE_TOOLS}
    assert set(role_tools.FUNCTIONS) == bundled, "every registry function belongs to some bundle"
    assert set(BROWSER) == set(BROWSER_ALL) and set(BROWSER_READ) <= set(BROWSER_ALL)


def test_every_bundle_tool_is_classified_for_the_harness():
    from backend.harness import tool_meta

    for slug in role_tools.FUNCTIONS:
        assert tool_meta.is_known(slug), slug


def test_registered_schemas_are_callable_by_name(home):
    role_tools.ensure_role_tools()
    names = {t["slug"]: t for t in db.tools_for_agent("pixel")}
    assert names["browser_set_viewport"]["handler_path"].endswith("qa_tools:browser_set_viewport")


def test_a_cats_turn_receives_and_can_run_its_registry_tools(home):
    """The path an agent turn uses: bind the cat's tools, list schemas, execute by name."""
    from backend.harness import deferred
    from backend.platform.tool_registry import register_agent_tools

    role_tools.ensure_role_tools()
    deferred.restore_loaded_tools([], {})
    bound = register_agent_tools("pixel")
    assert set(BROWSER) <= set(bound)
    offered = {s["function"]["name"] for s in deferred.extra_tool_schemas()}
    assert {"browser_snapshot", "browser_inspect", "browser_set_viewport"} <= offered
    # Executes through the same dispatcher; no session yet, so a plain-text error, not a crash.
    out = deferred.execute_loaded_tool("browser_snapshot", {})
    assert isinstance(out, str) and out.startswith("Error: no active browser session")

    deferred.restore_loaded_tools([], {})
    register_agent_tools("lex")
    offered = {s["function"]["name"] for s in deferred.extra_tool_schemas()}
    assert set(BROWSER_READ) <= offered and "data_collection_scan" in offered
    assert not offered & {"browser_click", "browser_type"}
    deferred.restore_loaded_tools([], {})  # loaded tools are process-wide: leave none for the next test


def test_an_extra_tool_already_placed_is_left_alone(home):
    role_tools.ensure_role_tools()
    from backend.platform.tool_registry import register_tool_function
    from backend.tool_functions import qa_tools

    tool_id = register_tool_function("close_all_sessions", qa_tools.close_all_sessions, type="system")
    db.assign_tool("aegis", tool_id)
    role_tools.place_extra_tools()
    assert not db.get_tool_by_slug("close_all_sessions")["is_global"]
    db.unassign_tool("aegis", tool_id)
    role_tools.place_extra_tools()
    assert db.get_tool_by_slug("close_all_sessions")["is_global"]
