"""Role tools are registered at start-up and respect what the user unassigned."""

from __future__ import annotations

import pytest

from backend.db import ledger as db
from backend.platform import role_tools

BROWSER = [slug for slug, _ in role_tools._BROWSER_TOOLS]


def _slugs(agent: str) -> set[str]:
    return {t["slug"] for t in db.tools_for_agent(agent)}


@pytest.fixture
def home(isolated):
    db.init_db()
    return isolated


def test_fresh_install_gives_aegis_and_pixel_the_browser_set(home):
    role_tools.ensure_role_tools()
    assert set(BROWSER) <= _slugs("aegis")
    assert set(BROWSER) <= _slugs("pixel")
    assert {"http_endpoint_audit", "run_test_suite"} <= _slugs("aegis")
    assert "browser_click" not in _slugs("pulse")
    assert "browser_click" not in _slugs("lex")
    assert db.get_meta("role_tools_version") == str(role_tools.ROLE_TOOLS_VERSION)
    assert not (home / "catcrew_home").exists()  # no stray files outside the database


def test_a_tool_the_user_removed_is_not_added_back(home):
    role_tools.ensure_role_tools()
    tool = db.get_tool_by_slug("browser_click")
    db.unassign_tool("pixel", tool["id"])
    role_tools.ensure_role_tools()
    role_tools.ensure_role_tools()
    assert "browser_click" not in _slugs("pixel")
    assert "browser_click" in _slugs("aegis")


def test_existing_install_is_upgraded_once(home):
    # An install that ran the old seed by hand: Aegis has the 5 original tools, no marker.
    old = ["browser_navigate", "browser_click", "browser_type", "browser_select_option", "browser_take_screenshot"]
    for bot, slug, fn in role_tools.ROLE_TOOLS:
        if bot == "aegis" and slug in old:
            role_tools.register_tool_function(slug, fn, type="system")
            db.assign_tool_by_slug("aegis", slug)
    # The user's choice: an *optional* tool switched off (essential ones cannot be, see
    # tests/test_essential_tools.py).
    db.unassign_tool("aegis", db.get_tool_by_slug("browser_select_option")["id"])

    role_tools.ensure_role_tools()
    assert set(BROWSER) - {"browser_select_option"} <= _slugs("aegis")
    assert "browser_select_option" not in _slugs("aegis")
    assert set(BROWSER) <= _slugs("pixel")

    db.unassign_tool("pixel", db.get_tool_by_slug("browser_click")["id"])
    role_tools.ensure_role_tools()
    assert "browser_click" not in _slugs("pixel")


def test_force_reassigns_everything(home):
    role_tools.ensure_role_tools()
    db.unassign_tool("aegis", db.get_tool_by_slug("browser_click")["id"])
    role_tools.ensure_role_tools(force=True)
    assert "browser_click" in _slugs("aegis")


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
    assert not {s["function"]["name"] for s in deferred.extra_tool_schemas()} & set(BROWSER)


def test_version_2_install_gets_browser_look_once(home):
    role_tools.ensure_role_tools()
    tool = db.get_tool_by_slug("browser_look")
    for bot in ("aegis", "pixel"):
        db.unassign_tool(bot, tool["id"])
    db.set_meta("role_tools_version", "2")  # what an install from before Vision has
    role_tools.ensure_role_tools()
    assert "browser_look" in _slugs("aegis") and "browser_look" in _slugs("pixel")
    db.unassign_tool("pixel", tool["id"])
    role_tools.ensure_role_tools()
    assert "browser_look" not in _slugs("pixel")  # the user's removal sticks
