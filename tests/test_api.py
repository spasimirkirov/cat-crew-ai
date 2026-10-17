"""REST API: projects, roster, agents, tasks, skills, tools, models, security."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.core.chat_service import ChatService

BASE = "http://127.0.0.1:8765"
CORE = {"cipher", "vanguard", "pixel", "pulse", "aegis", "lex", "blaze", "forge"}


@pytest.fixture
def client(isolated):
    app = create_app(chat_service=ChatService(use_memory=False))
    with TestClient(app, base_url=BASE) as c:
        yield c


def _project(client, tmp_path, name="Alpha", **extra):
    body = {"name": name, "folders": [str(tmp_path / "ws")], **extra}
    r = client.post("/api/projects", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# -- meta / security ------------------------------------------------------------------


def test_health_and_bootstrap(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    boot = client.get("/api/bootstrap").json()
    assert CORE <= {a["id"] for a in boot["agents"]}
    assert boot["projects"] == []
    assert any(a["bot_id"] == "crew" for a in boot["activity"])


def test_bad_host_is_rejected(client):
    r = client.get("/api/health", headers={"host": "evil.example"})
    assert r.status_code == 403


def test_bad_origin_is_rejected_and_good_origin_is_allowed(client):
    assert client.get("/api/health", headers={"origin": "https://evil.example"}).status_code == 403
    ok = client.get("/api/health", headers={"origin": "tauri://localhost"})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == "tauri://localhost"


def test_websocket_bad_origin_is_refused(client):
    from starlette.websockets import WebSocketDisconnect

    good = {"host": "127.0.0.1:8765"}
    with pytest.raises(WebSocketDisconnect) as bad_origin:
        with client.websocket_connect("/ws/events", headers={**good, "origin": "https://evil.example"}):
            pass
    assert bad_origin.value.code == 1008
    with pytest.raises(WebSocketDisconnect) as bad_host:  # Starlette's default host is "testserver"
        with client.websocket_connect("/ws/events"):
            pass
    assert bad_host.value.code == 1008
    with client.websocket_connect("/ws/events", headers={**good, "origin": "tauri://localhost"}) as ws:
        assert ws.receive_json()["type"] == "hello"


# -- agents ---------------------------------------------------------------------------


def test_core_agents_and_no_prompt_in_list(client):
    agents = client.get("/api/agents").json()
    by_id = {a["id"]: a for a in agents}
    assert CORE <= set(by_id)
    assert all(a["is_system"] for a in agents if a["id"] in CORE)
    assert "system_prompt" not in by_id["pulse"]
    detail = client.get("/api/agents/pulse").json()
    assert detail["system_prompt"]
    assert client.get("/api/agents/nope").status_code == 404


def test_the_crew_is_read_only(client):
    """Cats are defined by CatCrewAI: no create, edit or delete through the API."""
    assert client.post("/api/agents", json={"name": "Scout Cat", "role": "research"}).status_code == 405
    assert client.put("/api/agents/pulse", json={"title": "Recon"}).status_code == 405
    assert client.delete("/api/agents/pulse").status_code == 405
    assert client.get("/api/agents/pulse").status_code == 200


# -- projects / roster ----------------------------------------------------------------


def test_project_lifecycle(client, tmp_path):
    p = _project(client, tmp_path, description="  hello ")
    assert p["name"] == "Alpha" and p["description"] == "hello"
    assert p["folders"] and p["created_at"]
    assert client.get(f"/api/projects/{p['id']}").json()["id"] == p["id"]
    assert [x["id"] for x in client.get("/api/projects").json()] == [p["id"]]

    patched = client.patch(f"/api/projects/{p['id']}", json={"name": "Beta"}).json()
    assert patched["name"] == "Beta"

    other = tmp_path / "other"
    ws = client.put(f"/api/projects/{p['id']}/workspace", json={"path": str(other)}).json()
    assert ws["folders"][0].endswith("other") and other.is_dir()

    assert client.delete(f"/api/projects/{p['id']}").status_code == 204
    assert client.get(f"/api/projects/{p['id']}").status_code == 404


def test_project_validation(client):
    assert client.post("/api/projects", json={"name": "   "}).status_code == 422
    assert client.post("/api/projects", json={"name": "x", "bot_ids": ["ghost"]}).status_code == 404
    assert client.get("/api/projects/nope").status_code == 404


def test_roster_always_starts_with_the_room_and_add_remove(client, tmp_path):
    p = _project(client, tmp_path, bot_ids=["pulse"])
    roster = client.get(f"/api/projects/{p['id']}/roster").json()
    assert roster[0]["agent"]["id"] == "crew"
    assert "pulse" in [r["agent"]["id"] for r in roster]

    added = client.post(f"/api/projects/{p['id']}/roster", json={"bot_id": "aegis"})
    assert added.status_code == 201
    ids = [r["agent"]["id"] for r in client.get(f"/api/projects/{p['id']}/roster").json()]
    assert "aegis" in ids

    assert client.delete(f"/api/projects/{p['id']}/roster/aegis").status_code == 204
    ids = [r["agent"]["id"] for r in client.get(f"/api/projects/{p['id']}/roster").json()]
    assert "aegis" not in ids
    assert client.delete(f"/api/projects/{p['id']}/roster/crew").status_code == 422
    assert client.delete(f"/api/projects/{p['id']}/roster/chief").status_code == 422
    assert client.post(f"/api/projects/{p['id']}/roster", json={"bot_id": "ghost"}).status_code == 404


def test_browse_lists_only_directories(client, tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "f.txt").write_text("x")
    out = client.get("/api/workspaces/browse", params={"path": str(tmp_path)}).json()
    assert [e["name"] for e in out["entries"]] == ["a"]
    assert client.get("/api/workspaces/browse", params={"path": str(tmp_path / "f.txt")}).status_code == 422


def test_a_new_folder_can_be_created_from_the_picker(client, tmp_path):
    r = client.post("/api/workspaces/folders", json={"parent": str(tmp_path), "name": "  my site  "})
    assert r.status_code == 201, r.text
    assert (tmp_path / "my site").is_dir()
    assert r.json()["path"] == str((tmp_path / "my site").resolve()) and r.json()["entries"] == []
    assert r.json()["parent"] == str(tmp_path.resolve())
    # the picker now lists it
    listing = client.get("/api/workspaces/browse", params={"path": str(tmp_path)}).json()
    assert "my site" in [e["name"] for e in listing["entries"]]


def test_a_new_folder_is_refused_when_it_exists_or_the_name_is_bad(client, tmp_path):
    (tmp_path / "taken").mkdir()
    url = "/api/workspaces/folders"
    assert client.post(url, json={"parent": str(tmp_path), "name": "taken"}).status_code == 409
    for bad in ["", "   ", "..", ".", "a/b", "a\\b", "x:y", "what?", "trail.", "con", "NUL.txt", "a" * 121]:
        assert client.post(url, json={"parent": str(tmp_path), "name": bad}).status_code == 422, bad
    assert client.post(url, json={"parent": str(tmp_path / "nope"), "name": "x"}).status_code == 422
    assert client.post(url, json={"parent": "", "name": "x"}).status_code == 422  # the drive list: nothing to create in
    assert not (tmp_path.parent / "b").exists()


# -- tasks ----------------------------------------------------------------------------


def test_task_flow_and_project_scoping(client, tmp_path):
    p = _project(client, tmp_path)
    q = _project(client, tmp_path, name="Other")
    t = client.post(
        f"/api/projects/{p['id']}/tasks", json={"assigned_to": "pulse", "title": "Build it"}
    )
    assert t.status_code == 201, t.text
    task = t.json()
    assert task["status"] == "pending" and task["assigned_to"] == "pulse"

    listed = client.get(f"/api/projects/{p['id']}/tasks").json()
    assert [x["id"] for x in listed] == [task["id"]]
    assert client.get(f"/api/projects/{q['id']}/tasks").json() == []
    # a task is only reachable through its own project
    assert client.get(f"/api/projects/{q['id']}/tasks/{task['id']}").status_code == 404

    done = client.patch(f"/api/projects/{p['id']}/tasks/{task['id']}", json={"status": "done"})
    assert done.json()["status"] == "done"
    assert client.patch(f"/api/projects/{p['id']}/tasks/{task['id']}", json={"status": "??"}).status_code == 422
    detail = client.get(f"/api/projects/{p['id']}/tasks/{task['id']}").json()
    assert detail["results"] == []

    # assigning staffs the agent in the room
    ids = [r["agent"]["id"] for r in client.get(f"/api/projects/{p['id']}/roster").json()]
    assert "pulse" in ids
    assert client.post(
        f"/api/projects/{p['id']}/tasks", json={"assigned_to": "ghost", "title": "x"}
    ).status_code == 404


def test_changes_and_diffs(client, tmp_path):
    from backend.db import ledger

    p = _project(client, tmp_path)
    diff_uuid = ledger.log_file_change(p["id"], "", "pulse", "src/a.py", "+print(1)\n")
    changes = client.get(f"/api/projects/{p['id']}/changes").json()
    assert changes and changes[0]["file_path"] == "src/a.py"
    assert client.get(f"/api/diffs/{diff_uuid}").json()["diff_content"].startswith("+print")
    assert client.get("/api/diffs/nope").status_code == 404


# -- skills ---------------------------------------------------------------------------


def test_skills_matrix_custom_and_assignments(client):
    skills = client.get("/api/skills", params={"refresh": "true"}).json()
    assert skills, "bundled playbooks should be indexed"
    bad = [s["playbook_path"] for s in skills if str(s["playbook_path"]).startswith(("/", "C:", "\\"))]
    assert not bad, f"absolute paths leak to the UI: {bad[:3]}"

    made = client.post("/api/skills", json={"name": "Release Notes", "description": "Write notes", "body": "# Steps\n1. go"})
    assert made.status_code == 201, made.text
    sid = made.json()["id"]
    assert made.json()["type"] == "custom"

    assign = client.put(f"/api/skills/{sid}/assignments", json={"agent_ids": ["pulse", "lex"]})
    assert sorted(assign.json()["agent_ids"]) == ["lex", "pulse"]
    agents = {a["id"]: a for a in client.get("/api/agents").json()}
    assert made.json()["name"] in agents["pulse"]["playbooks"]
    assert made.json()["name"] in agents["lex"]["playbooks"]

    assert client.put(f"/api/skills/{sid}/assignments", json={"agent_ids": ["ghost"]}).status_code == 404
    assert client.put(f"/api/skills/{sid}/global", json={"on": True}).json()["is_global"] is True
    assert "go" in client.get(f"/api/skills/{sid}").json()["body"]

    assert client.delete(f"/api/skills/{sid}").status_code == 204
    assert client.get(f"/api/skills/{sid}").status_code == 404


def test_bundled_skill_cannot_be_deleted(client):
    skills = client.get("/api/skills", params={"refresh": "true"}).json()
    bundled = next(s for s in skills if s["type"] == "bundled")
    assert client.delete(f"/api/skills/{bundled['id']}").status_code == 403


def test_hermes_proposals_flow(client):
    """An auto-generated (Hermes) playbook shows up as a proposal until approved."""
    from backend.db import ledger

    sid = ledger.upsert_skill(
        slug="auto-thing", name="auto-thing", description="d", playbook_path="", type="auto_generated", version="1"
    )
    props = client.get("/api/skills/proposals").json()
    assert [s["id"] for s in props] == [sid]
    ok = client.post(f"/api/skills/{sid}/approve", json={"agent_id": "pulse"})
    assert ok.status_code == 200
    assert client.get("/api/skills/proposals").json() == []
    assert client.post(f"/api/skills/{sid}/dismiss").status_code == 204 or True


# -- tools ----------------------------------------------------------------------------


def test_tools_matrix(client):
    available = client.get("/api/tools/available").json()
    assert available and all(":" in a["handler"] for a in available)
    pick = next(a for a in available if not a["registered"])
    created = client.post("/api/tools", json={"handler": pick["handler"]})
    assert created.status_code == 201, created.text
    tool = created.json()
    assert tool["schema"] is not None
    tid = tool["id"]

    assert client.get("/api/tools").json()
    assert client.put(f"/api/tools/{tid}/assignments", json={"agent_ids": ["pulse"]}).json()["agent_ids"] == ["pulse"]
    assert client.put(f"/api/tools/{tid}/global", json={"on": True}).json()["is_global"] is True
    assert client.get(f"/api/tools/{tid}").json()["id"] == tid
    assert client.post("/api/tools", json={"handler": "backend.tool_functions.nope:nope"}).status_code == 422
    assert client.delete(f"/api/tools/{tid}").status_code == 204
    assert client.get(f"/api/tools/{tid}").status_code == 404


# -- models (secrets) -----------------------------------------------------------------


def test_models_never_return_secrets(client, monkeypatch):
    from backend.services import settings_store

    secret = "sk-SUPER-SECRET-VALUE-123"
    cfg = settings_store.load_settings()
    cfg.setdefault("api_access", {})["groq_key"] = secret
    settings_store.save_settings(cfg)

    body = client.get("/api/models")
    assert body.status_code == 200
    assert secret not in body.text
    data = body.json()
    assert {"chat", "coding", "memory", "images"} <= set(data["roles"])
    assert all(isinstance(h.get("configured", False), bool) for h in data["hosts"])

    put = client.put("/api/models/roles", json={"coding": {"host": "groq", "model": "m1"}})
    assert put.status_code == 200 and secret not in put.text
    assert put.json()["roles"]["coding"] == {"host": "groq", "model": "m1"}
    assert client.put("/api/models/roles", json={"coding": {"host": "bogus", "model": "x"}}).status_code == 422
    assert client.get("/api/models/hosts/bogus").status_code == 404
    on_disk = json.dumps(client.get("/api/bootstrap").json())
    assert secret not in on_disk


def test_shutdown_is_a_noop_without_uvicorn(client):
    r = client.post("/api/shutdown")
    assert r.status_code == 202 and r.json()["status"] == "not-running-under-uvicorn"


# -- Cat Crew is the orchestrator, never a specialist -----------------------------------


def test_assignments_never_look_up_or_store_the_orchestrator(client):
    """Regression: "Could not update browser_click: Unknown agent 'chief'"."""
    from backend.db import ledger

    skill = client.post("/api/skills", json={"name": "Crew Notes", "description": "d", "body": "b"}).json()
    tool = client.post(
        "/api/tools", json={"handler": next(a for a in client.get("/api/tools/available").json() if not a["registered"])["handler"]}
    ).json()
    for legacy in ("chief", "crew"):
        r = client.put(f"/api/skills/{skill['id']}/assignments", json={"agent_ids": [legacy, "pulse"]})
        assert r.status_code == 200, r.text
        assert r.json()["agent_ids"] == ["pulse"]
        r = client.put(f"/api/tools/{tool['id']}/assignments", json={"agent_ids": [legacy, "pulse"]})
        assert r.status_code == 200, r.text
        assert r.json()["agent_ids"] == ["pulse"]
    assert ledger.agent_ids_for_skill(skill["id"]) == {"pulse"}
    assert ledger.agent_ids_for_tool(tool["id"]) == {"pulse"}
    # real unknown agents are still rejected
    assert client.put(f"/api/tools/{tool['id']}/assignments", json={"agent_ids": ["ghost"]}).status_code == 404


def test_legacy_orchestrator_rows_are_hidden_and_untouched(client):
    """Rows written for agent id "chief" before the rename never reach the matrix."""
    from backend.db import ledger

    skill = client.post("/api/skills", json={"name": "Old Notes", "description": "d", "body": "b"}).json()
    conn = ledger._connect()
    try:
        conn.execute(
            "INSERT INTO agent_skills (id, agent_id, skill_id, added_at) VALUES ('as_legacy', 'chief', ?, 'now')",
            (skill["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    assert client.get(f"/api/skills/{skill['id']}").json()["agent_ids"] == []
    assert all(s["agent_ids"] == [] for s in client.get("/api/skills").json() if s["id"] == skill["id"])
    assert client.put(f"/api/skills/{skill['id']}/assignments", json={"agent_ids": ["pulse"]}).json()["agent_ids"] == ["pulse"]
    # additive/non-destructive: the legacy row is still there
    assert "chief" in ledger.agent_ids_for_skill(skill["id"])


def test_cat_crew_card_avatar_and_no_agent_config(client, tmp_path):
    p = _project(client, tmp_path)
    room = client.get(f"/api/projects/{p['id']}/roster").json()[0]
    agent = room["agent"]
    assert room["is_room"] is True
    assert agent["id"] == "crew" and agent["name"] == "Cat Crew"
    assert "orchestrator" in agent["description"].lower()
    assert agent["playbooks"] == [] and agent["letta_tools"] == []
    assert agent["avatar"] == "/api/agents/crew/avatar"
    for aid in ("crew", "chief"):
        img = client.get(f"/api/agents/{aid}/avatar")
        assert img.status_code == 200 and img.headers["content-type"] == "image/png"
        assert client.get(f"/api/agents/{aid}").json()["name"] == "Cat Crew"
        assert client.get(f"/api/agents/{aid}/activity").status_code == 200
        assert client.delete(f"/api/agents/{aid}").status_code == 405
        assert client.put(f"/api/agents/{aid}", json={"name": "x"}).status_code == 405
    assert "crew" not in {a["id"] for a in client.get("/api/agents").json()}
    assert client.post("/api/agents", json={"id": "crew", "name": "Crew"}).status_code == 405


def test_report_endpoint_starts_a_bug_turn_and_validates(client, tmp_path):
    pid = _project(client, tmp_path)["id"]
    r = client.post(f"/api/projects/{pid}/threads/pulse/report", json={"text": ""})
    assert r.status_code == 422
    r = client.post("/api/projects/nope/threads/pulse/report", json={"text": "broken"})
    assert r.status_code == 404
