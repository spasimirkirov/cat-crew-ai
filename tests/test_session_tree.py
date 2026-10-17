from backend.services.session_tree import append_turn, ensure_tree, path_to_head, provider_history, set_head, tree_outline, visible_history


def test_ensure_tree_migrates_linear_messages() -> None:
    session = {
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "yo"},
        ]
    }
    ensure_tree(session)
    assert session["messages"][0]["parent"] is None
    assert session["messages"][1]["parent"] == session["messages"][0]["id"]
    assert session["head"] == session["messages"][1]["id"]
    assert all(msg.get("id") for msg in session["messages"])


def test_branch_keeps_other_turns() -> None:
    session = {"messages": []}
    u1 = append_turn(session, "user", "first")
    a1 = append_turn(session, "assistant", "ok")
    u2 = append_turn(session, "user", "second")
    append_turn(session, "assistant", "done")
    assert set_head(session, a1["id"])
    u3 = append_turn(session, "user", "other branch")
    ids = {msg["id"] for msg in session["messages"]}
    assert {u1["id"], a1["id"], u2["id"], u3["id"]}.issubset(ids)
    visible = [msg["content"] for msg in visible_history(session)]
    assert visible == ["first", "ok", "other branch"]
    path_ids = [msg["id"] for msg in path_to_head(session)]
    assert u2["id"] not in path_ids
    assert u3["parent"] == a1["id"]


def test_tree_outline_marks_head_and_depth() -> None:
    session = {"messages": []}
    append_turn(session, "user", "a")
    a1 = append_turn(session, "assistant", "b")
    append_turn(session, "user", "c")
    set_head(session, a1["id"])
    append_turn(session, "user", "d")
    rows = tree_outline(session)
    depths = [row["depth"] for row in rows]
    assert depths == [0, 1, 2, 2]
    heads = [row for row in rows if row["is_head"]]
    assert len(heads) == 1
    assert heads[0]["content"] == "d"
    on_path = [row["content"] for row in rows if row["on_path"]]
    assert on_path == ["a", "b", "d"]


def test_provider_history_strips_tree_metadata() -> None:
    session = {"messages": []}
    append_turn(session, "user", "hi")
    append_turn(session, "assistant", "yo")
    hist = provider_history(session)
    assert hist == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]
    assert "id" not in hist[0]
    assert "parent" not in hist[0]


def test_append_turn_stamps_created_at() -> None:
    session = {"messages": []}
    first = append_turn(session, "user", "hi")
    second = append_turn(session, "assistant", "yo")
    assert first.get("created_at")
    assert second.get("created_at")
    visible = visible_history(session)
    assert visible[0]["created_at"] == first["created_at"]
    assert visible[1]["created_at"] == second["created_at"]


def test_visible_history_keeps_activity_metadata() -> None:
    session = {"messages": []}
    append_turn(session, "user", "build the site")
    node = append_turn(session, "assistant", "Jayce is on it.")
    node["activity"] = {
        "elapsed_s": 90,
        "steps": [{"line": "Delegating to a team", "preview": "Jayce ran"}],
    }
    visible = visible_history(session)
    assert visible[-1]["activity"]["elapsed_s"] == 90
    from backend.services.session_tree import provider_history

    provider = provider_history(session)
    assert "activity" not in provider[-1]


def test_hidden_turns_skip_ui_but_reach_provider() -> None:
    from backend.services.session_tree import provider_history, tree_outline

    session = {"messages": []}
    append_turn(session, "user", "hello")
    append_turn(session, "assistant", "hi")
    append_turn(session, "user", "INTERNAL VERIFY", hidden=True)
    visible = [msg["content"] for msg in visible_history(session)]
    assert visible == ["hello", "hi"]
    provider = [msg["content"] for msg in provider_history(session)]
    assert provider == ["hello", "hi", "INTERNAL VERIFY"]
    outline = tree_outline(session)
    assert all("INTERNAL" not in str(row.get("content") or "") for row in outline)


def test_fork_copies_project_thread(monkeypatch) -> None:
    from backend.services.sessions import fork_session

    monkeypatch.setattr("backend.services.sessions.save_session", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("backend.services.sessions.apply_session_workspace", lambda *_args, **_kwargs: None)
    forked = fork_session(
        {
            "id": "abc123",
            "title": "Shop",
            "messages": [],
            "project_id": "p1",
            "bot_id": "designer",
            "folders": [],
        }
    )
    assert forked["id"] != "abc123"
    assert forked["project_id"] == "p1"
    assert forked["bot_id"] == "designer"
    assert forked["parent_id"] == "abc123"
