"""Checkpoints: a turn's file changes can be undone, newest turn first."""

from __future__ import annotations

import pytest

from backend import tools
from backend.harness import checkpoints, hooks
from backend.services.workspace import set_workspace


@pytest.fixture
def ws(tmp_path):
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "index.html").write_text("v1", encoding="utf-8")
    (folder / "old.txt").write_text("keep me", encoding="utf-8")
    set_workspace(folder)
    return folder


def _call(name: str, **arguments) -> str:
    """A tool call as the loop makes it: the before-hooks, then the tool."""
    ctx = hooks.ToolCallContext(name=name, arguments=arguments)
    assert hooks.run_before(ctx) is None
    return getattr(tools, name)(**ctx.arguments)


def test_restoring_a_turn_puts_back_changed_created_and_moved_files(ws) -> None:
    token = checkpoints.begin("build the page", ws)
    _call("write_file", path="index.html", content="v2")
    _call("write_file", path="new/about.html", content="about")
    _call("move_path", src="old.txt", dest="renamed.txt")
    _call("write_file", path="index.html", content="v3")  # the second change keeps the first copy
    checkpoints.end(token)

    [cp] = checkpoints.list_checkpoints(ws)
    assert cp["label"] == "build the page"
    assert {"index.html", "new/about.html", "old.txt", "renamed.txt"} <= set(cp["files"])

    result = checkpoints.restore(ws, cp["id"])
    assert (ws / "index.html").read_text() == "v1"
    assert not (ws / "new" / "about.html").exists()
    assert (ws / "old.txt").read_text() == "keep me" and not (ws / "renamed.txt").exists()
    assert result["undone"] == [cp["id"]]
    assert checkpoints.list_checkpoints(ws) == []


def test_restoring_an_earlier_turn_also_undoes_every_later_one(ws) -> None:
    for content in ("v2", "v3", "v4"):
        token = checkpoints.begin(f"make it {content}", ws)
        _call("write_file", path="index.html", content=content)
        checkpoints.end(token)
    newest_first = checkpoints.list_checkpoints(ws)
    assert [c["label"] for c in newest_first] == ["make it v4", "make it v3", "make it v2"]

    result = checkpoints.restore(ws, newest_first[1]["id"])  # back to before "v3"
    assert (ws / "index.html").read_text() == "v2"
    assert len(result["undone"]) == 2
    assert [c["label"] for c in checkpoints.list_checkpoints(ws)] == ["make it v2"]


def test_a_turn_that_changed_nothing_leaves_no_checkpoint_and_commands_are_listed(ws) -> None:
    token = checkpoints.begin("just look", ws)
    _call("read_file", path="index.html")
    checkpoints.end(token)
    assert checkpoints.list_checkpoints(ws) == []

    token = checkpoints.begin("install", ws)
    checkpoints.before_tool("run_command", {"command": "npm install"})
    checkpoints.end(token)
    [cp] = checkpoints.list_checkpoints(ws)
    assert cp["commands"] == ["run_command: npm install"]
    assert checkpoints.restore(ws, cp["id"])["not_undone"] == ["run_command: npm install"]


def test_a_subagent_shares_its_parents_checkpoint(ws) -> None:
    outer = checkpoints.begin("parent", ws)
    assert checkpoints.begin("child", ws) is None
    _call("write_file", path="index.html", content="v2")
    checkpoints.end(outer)
    assert [c["label"] for c in checkpoints.list_checkpoints(ws)] == ["parent"]


def test_no_open_checkpoint_means_no_copies(ws) -> None:
    _call("write_file", path="index.html", content="v2")
    assert checkpoints.list_checkpoints(ws) == []


def test_unknown_checkpoint_raises(ws) -> None:
    with pytest.raises(KeyError):
        checkpoints.restore(ws, "nope")


def test_api_lists_and_restores_and_refuses_while_the_crew_works(ws, isolated) -> None:
    from fastapi.testclient import TestClient

    from backend.api.app import create_app
    from backend.platform.registry import load_registry, save_registry
    from backend.platform.models import Project

    registry = load_registry(refresh=True)
    registry.projects["p1"] = Project(id="p1", name="Site", folders=[str(ws)])
    save_registry(registry)

    token = checkpoints.begin("restyle", ws)
    _call("write_file", path="index.html", content="v2")
    checkpoints.end(token)

    with TestClient(create_app(), base_url="http://127.0.0.1:8765") as client:
        [cp] = client.get("/api/projects/p1/checkpoints").json()
        assert cp["files"] == ["index.html"]
        client.app.state.chat.is_busy = lambda project_id=None: True
        assert client.post(f"/api/projects/p1/checkpoints/{cp['id']}/restore").status_code == 409
        client.app.state.chat.is_busy = lambda project_id=None: False
        res = client.post(f"/api/projects/p1/checkpoints/{cp['id']}/restore")
        assert res.status_code == 200, res.text
        assert res.json()["files"] == ["restored index.html"]
        assert (ws / "index.html").read_text() == "v1"
        assert client.post("/api/projects/p1/checkpoints/nope/restore").status_code == 404
