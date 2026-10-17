from jinx_ai.providers.openai_compat import friendly_provider_error


def test_refused_local_port_is_human() -> None:
    dump = (
        "HTTPConnectionPool(host='127.0.0.1', port=8787): Max retries exceeded with url: "
        "/v1/chat/completions (Caused by NewConnectionError("
        "\"<urllib3.connection.HTTPConnection object at 0x1>: Failed to establish a new "
        "connection: [WinError 10061] No connection could be made because the target machine "
        "actively refused it\"))"
    )
    text = friendly_provider_error(dump, display_name="Local")
    assert "HTTPConnectionPool" not in text
    assert "WinError" not in text
    assert "llama-server is not running" in text
    assert "Hugging Face Hub" in text
    assert "Launch" in text


def test_already_friendly_stays() -> None:
    msg = (
        "llama-server is not running, so there is no local model to talk to. "
        "Open AI Models → Local and click Launch."
    )
    assert friendly_provider_error(msg, display_name="Local") == msg


def test_ensure_ready_skips_when_local_off(monkeypatch) -> None:
    from jinx_ai import llama_server

    monkeypatch.setattr(llama_server, "local_enabled", lambda: False)
    try:
        llama_server.ensure_ready("x")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "Local is off" in str(exc)


def test_ensure_ready_needs_a_gguf(monkeypatch) -> None:
    from jinx_ai import llama_server

    monkeypatch.setattr(llama_server, "local_enabled", lambda: True)
    monkeypatch.setattr(llama_server, "_chat_ggufs", lambda: [])
    try:
        llama_server.ensure_ready("x")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "No GGUF" in str(exc)


def test_ensure_ready_starts_and_loads(monkeypatch) -> None:
    from jinx_ai import llama_server

    monkeypatch.setattr(llama_server, "local_enabled", lambda: True)
    monkeypatch.setattr(llama_server, "_chat_ggufs", lambda: [object()])
    monkeypatch.setattr(llama_server, "ping_server", lambda: False)
    monkeypatch.setattr(llama_server, "launch_server", lambda: "started")
    monkeypatch.setattr(llama_server, "list_loaded", lambda: [])
    monkeypatch.setattr(llama_server, "load_slot", lambda name: f"Loading {name}")
    monkeypatch.setattr(llama_server, "_model_is_loaded", lambda name, loaded: False)
    assert llama_server.ensure_ready("gemma") == "Loading gemma"


def test_fit_chat_messages_merges_stacked_users() -> None:
    from jinx_ai.providers.openai_compat import fit_chat_messages

    out = fit_chat_messages(
        [
            {"role": "system", "content": "You are Jinx."},
            {"role": "user", "content": "Hey Jinx"},
            {"role": "user", "content": "Hi jinx!"},
            {"role": "user", "content": "Hi"},
        ]
    )
    assert [msg["role"] for msg in out] == ["user"]
    assert "You are Jinx." in out[0]["content"]
    assert out[0]["content"].endswith("Hi")


def test_fit_chat_messages_keeps_alternation() -> None:
    from jinx_ai.providers.openai_compat import fit_chat_messages

    out = fit_chat_messages(
        [
            {"role": "system", "content": "You are Jinx."},
            {"role": "user", "content": "Hey"},
            {"role": "assistant", "content": "Hello"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert [msg["role"] for msg in out] == ["user", "assistant", "user"]
    assert out[0]["content"].startswith("You are Jinx.")
    assert "Hey" in out[0]["content"]
