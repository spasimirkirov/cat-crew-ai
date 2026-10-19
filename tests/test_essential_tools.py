"""Each cat's bundle (platform/essentials.py) is always on; the user does not pick it."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.db import ledger as db
from backend.platform import role_tools, tool_registry
from backend.platform.essentials import (
    ESSENTIAL_BUILTINS,
    ESSENTIAL_ROLE_TOOLS,
    essential_agents_for_slug,
    essential_slugs_for_agent,
)
from backend.platform.toolset import ROLE_PACKS, builtin_names_for_role
from backend.tools import TOOLS_SCHEMA


def test_every_essential_builtin_is_a_real_tool() -> None:
    known = {(t.get("function") or {}).get("name") for t in TOOLS_SCHEMA}
    for role, names in ESSENTIAL_BUILTINS.items():
        missing = [n for n in names if n not in known]
        assert not missing, f"{role}: {missing} are not built-in tools"


def test_packs_contain_the_essentials_and_an_agent_tools_list_cannot_remove_them() -> None:
    for role, essential in ESSENTIAL_BUILTINS.items():
        assert set(essential) <= set(ROLE_PACKS[role])
        narrowed = builtin_names_for_role(role, ["web_search"])  # an explicit list of one optional tool
        assert set(essential) <= set(narrowed), role
        assert "web_search" in narrowed


def test_every_specialist_can_report_its_task() -> None:
    for role in ("vanguard", "prompt_engineer", "designer", "developer", "qa", "devops", "legal", "marketing"):
        assert "report_task_results" in builtin_names_for_role(role), role


@pytest.fixture
def home(isolated):
    db.init_db()
    role_tools.ensure_role_tools()
    return isolated


def _slugs(agent: str) -> set[str]:
    return {t["slug"] for t in db.tools_for_agent(agent)}


def test_startup_reattaches_every_bundle_tool(home) -> None:
    db.unassign_tool("aegis", db.get_tool_by_slug("browser_click")["id"])
    db.unassign_tool("aegis", db.get_tool_by_slug("browser_look")["id"])
    role_tools.ensure_role_tools()
    assert {"browser_click", "browser_look"} <= _slugs("aegis")


def test_essential_tools_are_bound_for_the_turn_even_without_an_assignment_row(home) -> None:
    db.unassign_tool("pulse", db.get_tool_by_slug("write_file_patch")["id"])
    assert "write_file_patch" not in _slugs("pulse")
    names = tool_registry.register_agent_tools("pulse")
    assert "write_file_patch" in names


def test_essentials_reference_registered_tools(home) -> None:
    for bot_id, slug in ESSENTIAL_ROLE_TOOLS:
        assert db.get_tool_by_slug(slug) is not None, (bot_id, slug)
    assert essential_slugs_for_agent("pulse") == ("git_diff_inspector", "http_endpoint_audit", "read_code_ast", "write_file_patch")
    assert essential_agents_for_slug("browser_close") == {"aegis", "pixel", "blaze", "lex"}
    assert essential_slugs_for_agent("cipher") == ()


@pytest.fixture
def client(home):
    with TestClient(create_app(), base_url="http://127.0.0.1:8765") as c:
        yield c


def _tool(client, slug):
    return next(t for t in client.get("/api/tools").json() if t["name"] == slug or t["handler"].endswith(":" + slug))


def test_api_marks_essentials_and_refuses_to_unassign_or_delete_them(client) -> None:
    tool = _tool(client, "write_file_patch")
    assert tool["locked_agent_ids"] == ["pulse"]
    res = client.put(f"/api/tools/{tool['id']}/assignments", json={"agent_ids": []})
    assert res.status_code == 200 and "pulse" in res.json()["agent_ids"]  # still on
    assert client.delete(f"/api/tools/{tool['id']}").status_code == 409


def test_a_registered_tool_in_no_bundle_goes_to_every_cat(client) -> None:
    res = client.post("/api/tools", json={"handler": "backend.tool_functions.qa_tools:close_all_sessions"})
    assert res.status_code == 201, res.text
    extra = res.json()
    assert extra["locked_agent_ids"] == []
    assert extra["is_global"] is True
    assert "close_all_sessions" in _slugs("lex")


def test_a_bound_registry_tool_actually_runs(home) -> None:
    """A registry tool (write_file_patch, the browser set, create_fix_task ...) is advertised in the
    cat's schema, so execute_tool must run it. It used to answer "unknown tool" for all of them,
    which the loop guard then reported as "got stuck repeating the same failed tool"."""
    from backend.tools import execute_tool

    tool_registry.register_agent_tools("aegis")
    out = execute_tool("http_endpoint_audit", {})  # bad arguments still reach the function
    assert "unknown tool" not in out
    assert execute_tool("no_such_tool", {}).startswith("Error: unknown tool")


def test_agent_api_lists_the_essentials_read_only(client) -> None:
    by_id = {a["id"]: a for a in client.get("/api/agents").json()}
    pulse = by_id["pulse"]["essential_tools"]
    assert len(pulse) == len(set(pulse)), "no duplicates"
    assert set(ESSENTIAL_BUILTINS["developer"]) <= set(pulse)  # built-ins of the role
    assert {"write_file_patch", "git_diff_inspector"} <= set(pulse)  # registry essentials for Pulse
    assert "browser_navigate" not in pulse  # not Pulse's
    assert "browser_navigate" in by_id["aegis"]["essential_tools"]
    assert client.get("/api/agents/pulse").json()["essential_tools"] == pulse
    from backend.api.views import ROOM_ID

    assert client.get(f"/api/agents/{ROOM_ID}").json()["essential_tools"] == []


# --- essential skills (playbooks) ------------------------------------------------------------


def _skill_slugs(agent: str) -> set[str]:
    return {s["slug"] for s in db.skills_for_agent(agent)}


def test_every_essential_skill_is_a_bundled_playbook() -> None:
    from backend.agent_context import find_skill
    from backend.platform.essentials import ESSENTIAL_SKILLS

    for bot_id, slug in ESSENTIAL_SKILLS:
        assert find_skill(slug) is not None, (bot_id, slug)


def test_startup_reattaches_every_bundle_skill_and_leaves_unneeded_ones_off(home) -> None:
    db.sync_skills_from_disk()
    role_tools.ensure_essential_skills()
    assert {"coding-companion", "static-web", "commit-style"} <= _skill_slugs("pulse")
    db.unassign_skill("pulse", db.get_skill_by_slug("static-web")["id"])
    role_tools.ensure_essential_skills()
    assert "static-web" in _skill_slugs("pulse")
    # A bundled playbook no cat needs stays off, and is not made global either.
    assert "desktop" not in _skill_slugs("pulse")
    assert not db.get_skill_by_slug("desktop")["is_global"]


def test_an_essential_skill_is_readable_and_indexed_even_without_its_row(home) -> None:
    from backend.agent_context import read_skill, skills_prompt_index

    db.sync_skills_from_disk()
    assert "static-web" not in _skill_slugs("pulse")
    assert read_skill("static-web", agent_id="pulse").startswith("# static-web")
    assert "not assigned to you" in read_skill("docker", agent_id="pulse")
    index = skills_prompt_index("pulse")
    assert "- static-web:" in index and "- coding-companion:" in index and "- commit-style:" in index
    assert "- docker:" not in index


def test_cat_crew_reads_its_own_playbooks_without_agent_rows(home) -> None:
    from backend.agent_context import read_skill, skills_prompt_index
    from backend.api.views import ROOM_ID

    db.sync_skills_from_disk()
    assert read_skill("catcrew-self", agent_id=ROOM_ID).startswith("# catcrew-self")
    assert "not assigned to you" in read_skill("laravel", agent_id=ROOM_ID)
    assert "- web-research:" in skills_prompt_index(ROOM_ID)


def test_skills_api_marks_essentials_and_refuses_to_unassign_them(client) -> None:
    db.sync_skills_from_disk()
    skills = {s["name"]: s for s in client.get("/api/skills").json()}
    web = skills["static-web"]
    assert web["locked_agent_ids"] == ["aegis", "forge", "pixel", "pulse", "vanguard"]
    res = client.put(f"/api/skills/{web['id']}/assignments", json={"agent_ids": []})
    assert res.status_code == 200 and {"pixel", "pulse"} <= set(res.json()["agent_ids"])
    assert skills["docker"]["locked_agent_ids"] == ["forge"]
    assert skills["desktop"]["locked_agent_ids"] == []


def test_a_new_skill_goes_to_the_cats_it_targets_or_to_every_cat(client) -> None:
    db.sync_skills_from_disk()
    targeted = client.post("/api/skills", json={"name": "Stripe checkout", "body": "x", "target_roles": "developer, qa"})
    assert targeted.status_code == 201, targeted.text
    assert sorted(targeted.json()["agent_ids"]) == ["aegis", "pulse"]
    assert targeted.json()["is_global"] is False
    anyone = client.post("/api/skills", json={"name": "House style", "body": "x"})
    assert anyone.json()["is_global"] is True


def test_every_cat_has_a_bundle_and_every_bundle_playbook_exists() -> None:
    from backend.platform.catcrew import CATCREW_MEMBERS
    from backend.platform.essentials import CREW_SKILLS, SKILL_BUNDLES
    from backend.agent_context import find_skill

    assert set(SKILL_BUNDLES) == {m["id"] for m in CATCREW_MEMBERS}
    for slug in CREW_SKILLS:
        assert find_skill(slug) is not None, slug
