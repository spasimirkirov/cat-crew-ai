"""The MCP subsystem: configuration, schema normalisation, connection handling and per-cat tool scoping.

Most tests talk to a real MCP server (``tests/fake_mcp_server.py``, official ``mcp`` SDK) over stdio,
streamable HTTP and SSE, so the SDK calls, the background loop and the reconnect logic run for real.
They are skipped when the installed ``mcp`` is not the v2 API.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.harness import approvals, deferred
from backend.services import mcp_features, mcp_service
from backend.services.mcp_service import (
    MCPManager,
    McpCallError,
    McpUnavailable,
    ServerConfig,
    explain,
    format_result,
    expand_path,
    function_name,
    load_config,
    openai_schema,
    parse_server,
    pick_exposed,
    public_url,
)

FAKE = str(Path(__file__).with_name("fake_mcp_server.py"))


def _sdk_ok() -> bool:
    try:
        import mcp

        return hasattr(mcp, "Client") and hasattr(mcp, "MCPError")
    except ImportError:
        return False


needs_sdk = pytest.mark.skipif(not _sdk_ok(), reason="needs the mcp v2 SDK (pip install -r requirements.txt)")


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_service, "_data_dir", lambda: tmp_path / "mcpdata")
    deferred.clear_loaded_tools()
    mcp_service.set_manager(None)
    yield
    mcp_service.set_manager(None)
    deferred.clear_loaded_tools()


def wait_for(predicate, timeout: float = 25.0, step: float = 0.1):
    end = time.time() + timeout
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise AssertionError("timed out waiting for the condition")


def cfg(server_id: str = "fake", *, transport: str = "stdio", **over) -> ServerConfig:
    raw: dict = {"label": "Fake MCP", "transport": transport, "bind_to": ["pixel"], "ping_interval": 2, "connect_timeout": 30}
    if transport == "stdio":
        raw.update(command=sys.executable, args=[FAKE, "stdio"])
    raw.update(over)
    return parse_server(server_id, raw)


@pytest.fixture
def make_manager():
    made: list[MCPManager] = []

    def build(*configs: ServerConfig, connect: bool = True) -> MCPManager:
        manager = MCPManager(list(configs))
        made.append(manager)
        manager.start()
        if connect:
            for c in configs:
                if c.enabled:
                    wait_for(lambda cid=c.id: manager.server(cid)["status"] == "connected")
        return manager

    yield build
    for m in made:
        m.stop()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def http_server():
    procs: list[subprocess.Popen] = []

    def start(kind: str, env: dict | None = None) -> int:
        port = free_port()
        proc = subprocess.Popen(
            [sys.executable, FAKE, kind, str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**os.environ, **(env or {})}
        )
        procs.append(proc)

        def up() -> bool:
            with socket.socket() as s:
                s.settimeout(0.3)
                return s.connect_ex(("127.0.0.1", port)) == 0

        wait_for(up, 20)
        return port

    yield start
    for p in procs:
        p.terminate()
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()


# --------------------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------------------


def test_the_shipped_config_binds_blender_to_pixel_and_unity_to_pulse_only():
    servers, problems = load_config()
    by_id = {s.id: s for s in servers}
    assert problems == []
    assert by_id["blender"].bind_to == ["pixel"] and by_id["blender"].transport == "stdio"
    assert by_id["unity"].bind_to == ["pulse"] and by_id["unity"].transport == "streamable-http"
    assert by_id["unity"].url == "http://localhost:8080/mcp"
    assert "compile_check" in by_id["unity"].features
    assert by_id["blender"].probe and by_id["unity"].probe


def test_the_shipped_config_has_the_unity_relay_as_a_second_pulse_door(monkeypatch, tmp_path):
    monkeypatch.delenv("CATCREW_UNITY_RELAY_COMMAND", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    servers, problems = load_config()
    relay = {s.id: s for s in servers}["unity_relay"]
    assert problems == []
    assert relay.transport == "stdio" and relay.args == ["--mcp"] and relay.bind_to == ["pulse"]
    assert relay.enabled and relay.skill == "unity-mcp"
    assert relay.command.startswith(str(tmp_path))  # "~" means the home folder
    assert relay.command.replace("\\", "/").endswith(".unity/relay/relay_win.exe")
    assert function_name("unity_relay", "get_scene") == "mcp__unity_relay__get_scene"


def test_the_unity_relay_command_can_be_overridden(monkeypatch):
    monkeypatch.setenv("CATCREW_UNITY_RELAY_COMMAND", "D:/tools/relay.exe")
    servers, _ = load_config()
    assert {s.id: s for s in servers}["unity_relay"].command == "D:/tools/relay.exe"


def test_a_leading_tilde_is_the_home_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert expand_path("~/x/y.exe").replace("\\", "/") == (str(tmp_path) + "/x/y.exe").replace("\\", "/")
    assert expand_path("a~b/c") == "a~b/c"  # only a leading ~ counts


def test_shipped_config_keeps_the_named_tools_in_front():
    servers, _ = load_config()
    by_id = {s.id: s for s in servers}
    for name in ("execute_blender_python", "get_scene_info", "export_fbx", "create_material", "render_frame"):
        assert name in by_id["blender"].priority
    for name in ("read_console", "refresh_unity", "manage_gameobject", "manage_build", "run_tests"):
        assert name in by_id["unity"].priority


def test_config_expands_environment_and_never_keeps_empty_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("T_PORT", "9911")
    monkeypatch.delenv("T_TOKEN", raising=False)
    monkeypatch.setenv("T_KEY", "s3cret-value")
    path = tmp_path / "m.json"
    path.write_text(
        json.dumps(
            {
                "servers": {
                    "_comment": "ignored",
                    "a": {
                        "command": "srv",
                        "args": ["--port", "${T_PORT}", "--x", "${T_NOPE:-dflt}"],
                        "env": {"KEY": "${T_KEY}", "EMPTY": "${T_NOPE}"},
                        "bind_to": ["Pixel", "pulse"],
                    },
                    "b": {"transport": "http", "url": "${T_URL:-http://localhost:1/mcp}", "headers": {"Authorization": "${T_TOKEN}"}},
                }
            }
        )
    )
    servers, problems = load_config(path)
    a, b = servers
    assert problems == []
    assert a.args == ["--port", "9911", "--x", "dflt"]
    assert a.env == {"KEY": "s3cret-value"}  # the empty one is dropped, not sent as ""
    assert a.bind_to == ["pixel", "pulse"]
    assert b.transport == "streamable-http" and b.url == "http://localhost:1/mcp"
    assert b.headers == {}  # an unset token is no header at all


def test_config_problems_are_sentences_and_do_not_stop_the_good_servers(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"servers": {"ok": {"command": "x"}, "Bad Id": {"command": "x"}, "t": {"transport": "carrier-pigeon", "command": "x"}, "noc": {"transport": "stdio"}, "u": {"transport": "sse", "url": "ftp://x"}}}))
    servers, problems = load_config(path)
    assert [s.id for s in servers] == ["ok"]
    assert len(problems) == 4
    assert all(isinstance(p, str) and p for p in problems)


def test_a_missing_or_broken_file_is_empty_not_fatal(tmp_path):
    assert load_config(tmp_path / "nope.json") == ([], [])
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_config(bad)[0] == []
    assert load_config(bad)[1]


def test_public_url_strips_credentials_query_and_fragment():
    assert public_url("http://user:pw@localhost:8080/mcp?token=abc#x") == "http://localhost:8080/mcp"
    assert "pw" not in public_url("https://a:pw@h/p")


# --------------------------------------------------------------------------------------------------
# Tool names and schemas
# --------------------------------------------------------------------------------------------------


def test_function_names_are_provider_safe_and_unique():
    assert function_name("blender", "scene.info") == "mcp__blender__scene_info"
    long = function_name("s", "x" * 100)
    assert len(long) <= 64 and long.startswith("mcp__s__")
    taken = {"mcp__s__t"}
    other = function_name("s", "t", taken)
    assert other != "mcp__s__t" and len(other) <= 64
    a, b = function_name("s", "x" * 100), function_name("s", "x" * 99 + "y")
    assert a != b


def test_schema_inlines_refs_drops_provider_hostile_keywords_and_collapses_optionals():
    raw = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "title": "Args",
        "additionalProperties": False,
        "$defs": {"Vec": {"type": "object", "properties": {"x": {"type": "number"}}, "required": ["x"], "additionalProperties": False}},
        "properties": {
            "loc": {"$ref": "#/$defs/Vec", "description": "where"},
            "name": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "title": "Name"},
            "mode": {"anyOf": [{"type": "integer"}, {"type": "string"}]},
            "title": {"type": "string", "description": "a real parameter called title"},
        },
        "required": ["loc", "ghost"],
    }
    out = openai_schema("mcp__s__t", "d" * 2000, raw)
    fn = out["function"]
    params = fn["parameters"]
    assert fn["name"] == "mcp__s__t" and len(fn["description"]) <= 700
    assert "$schema" not in params and "$defs" not in params and "additionalProperties" not in params and "title" not in params
    assert params["properties"]["loc"]["properties"]["x"]["type"] == "number"
    assert "additionalProperties" not in params["properties"]["loc"]
    assert params["properties"]["name"]["type"] == "string"
    assert params["properties"]["mode"]["type"] == "integer" and "string" in params["properties"]["mode"]["description"]
    assert "title" in params["properties"]  # a parameter *named* title survives
    assert params["required"] == ["loc"]  # required names that do not exist are dropped
    json.dumps(out)  # serialisable


def test_schema_of_a_tool_without_parameters_is_a_valid_empty_object():
    for raw in (None, {}, {"type": "string"}):
        params = openai_schema("f", "", raw)["function"]["parameters"]
        assert params == {"type": "object", "properties": {}}


def make_tool(server: str, name: str) -> mcp_service.McpTool:
    fname = function_name(server, name)
    return mcp_service.McpTool(server, name, fname, f"does {name}", openai_schema(fname, f"does {name}", {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}))


def test_tool_cap_puts_priority_tools_first_and_leaves_the_rest_on_demand():
    tools = [make_tool("b", n) for n in ("aaa", "bbb", "get_scene_info", "ccc", "execute_script", "ddd")]
    conf = cfg("b", max_tools=3, priority=["execute_*", "get_scene_info"], deny=["ddd"])
    shown, rest = pick_exposed(tools, conf)
    assert [t.name for t in shown][:2] == ["get_scene_info", "execute_script"]
    assert len(shown) == 3
    assert "ddd" not in [t.name for t in shown + rest]
    assert {t.name for t in shown + rest} == {"aaa", "bbb", "get_scene_info", "ccc", "execute_script"}


def test_allow_patterns_narrow_what_is_exposed():
    tools = [make_tool("u", n) for n in ("manage_scene", "manage_asset", "read_console", "generate_image")]
    shown, rest = pick_exposed(tools, cfg("u", allow=["manage_*", "read_console"]))
    assert {t.name for t in shown} == {"manage_scene", "manage_asset", "read_console"} and rest == []


# --------------------------------------------------------------------------------------------------
# Results and errors
# --------------------------------------------------------------------------------------------------


def block(kind, **kw):
    return SimpleNamespace(type=kind, **kw)


def test_format_result_joins_text_flags_errors_and_clips(monkeypatch):
    ok = SimpleNamespace(content=[block("text", text="one"), block("text", text="two")], is_error=False, structured_content=None)
    assert format_result("s", ok) == "one\ntwo"
    bad = SimpleNamespace(content=[block("text", text="nope")], is_error=True, structured_content=None)
    assert format_result("s", bad) == "Error: nope"
    empty = SimpleNamespace(content=[], is_error=False, structured_content={"a": 1})
    assert '"a": 1' in format_result("s", empty)
    assert "no output" in format_result("s", SimpleNamespace(content=[], is_error=False, structured_content=None))
    big = SimpleNamespace(content=[block("text", text="x" * 500)], is_error=False, structured_content=None)
    clipped = format_result("s", big, limit=100)
    assert clipped.startswith("x" * 100) and "truncated" in clipped and len(clipped) < 200


def test_format_result_saves_images_to_disk_and_returns_the_path():
    import base64

    png = base64.b64encode(b"\x89PNG-fake").decode()
    res = SimpleNamespace(content=[block("image", data=png, mime_type="image/png")], is_error=False, structured_content=None)
    text = format_result("blender", res)
    assert "image/png saved to" in text
    path = Path(text.split("saved to ")[1].split(" (")[0])
    assert path.is_file() and path.read_bytes() == b"\x89PNG-fake"
    assert "media" in path.parts and "blender" in path.parts


def test_explain_gives_a_sentence_per_failure_and_never_the_secret():
    c = cfg("u", transport="streamable-http", url="http://user:pw@127.0.0.1:9/mcp?key=topsecret")
    kind, msg = explain(ConnectionRefusedError("boom"), c)
    assert kind == "refused" and "127.0.0.1:9" in msg
    assert "pw" not in msg and "topsecret" not in msg
    kind, msg = explain(FileNotFoundError("[Errno 2] /secret/path/mcp-blender"), cfg("b"))
    assert kind == "not_found" and "secret" not in msg
    kind, _ = explain(TimeoutError(), c)
    assert kind == "timeout"
    group_type = globals().get("BaseExceptionGroup") or getattr(__import__("builtins"), "BaseExceptionGroup", None)
    if group_type is None:  # Python 3.10: anyio raises the exceptiongroup backport instead
        from exceptiongroup import BaseExceptionGroup as group_type
    group = group_type("g", [ConnectionRefusedError("x")])
    assert explain(group, c)[0] == "refused"
    kind, msg = explain(RuntimeError("weird with s3cret"), c)
    assert kind == "error" and "s3cret" not in msg


# --------------------------------------------------------------------------------------------------
# A real server over stdio
# --------------------------------------------------------------------------------------------------


@needs_sdk
def test_connects_over_stdio_lists_tools_and_calls_one(make_manager):
    manager = make_manager(cfg())
    view = manager.server("fake")
    assert view["status"] == "connected" and view["ready"] is True
    assert view["tools_total"] >= 8 and "add" in view["tool_names"]
    assert view["bound_to"] == ["pixel"] and view["transport"] == "stdio"
    assert manager.call("fake", "add", {"a": 2, "b": 5}) == "7"
    assert manager.call("fake", "peek", {}) == "peek:state"


@needs_sdk
def test_a_failing_tool_comes_back_as_an_error_string_not_an_exception(make_manager):
    manager = make_manager(cfg())
    out = manager.call("fake", "boom", {})
    assert out.startswith("Error:")
    with pytest.raises(McpUnavailable):
        manager.call("nosuch", "x", {})


@needs_sdk
def test_images_are_saved_and_referenced(make_manager):
    manager = make_manager(cfg())
    out = manager.call("fake", "snap", {})
    assert "image/png saved to" in out
    assert Path(out.split("saved to ")[1].split(" (")[0]).is_file()


@needs_sdk
def test_stdio_env_reaches_the_server_but_never_the_status(make_manager, tmp_path):
    flag = tmp_path / "closed.flag"
    flag.write_text("x")
    conf = cfg(env={"FAKE_FLAG": str(flag), "SECRET_TOKEN": "hunter2-token"}, probe={"tool": "bridge"})
    manager = make_manager(conf)
    wait_for(lambda: manager.server("fake")["bridge"] == "down")  # the env var was seen by the server
    blob = json.dumps(manager.status())
    assert "hunter2" not in blob and str(flag) not in blob


@needs_sdk
def test_the_probe_says_whether_the_program_behind_the_server_answers(make_manager, tmp_path):
    flag = tmp_path / "closed.flag"
    manager = make_manager(cfg(env={"FAKE_FLAG": str(flag)}, probe={"tool": "bridge"}, app_label="Blender"))
    wait_for(lambda: manager.server("fake")["ready"])
    assert manager.server("fake")["bridge"] == "ok"
    flag.write_text("closed")
    manager.refresh()
    wait_for(lambda: manager.server("fake")["bridge"] == "down")
    view = manager.server("fake")
    assert view["status"] == "connected" and view["ready"] is False
    assert "Blender does not answer" in view["note"]
    assert "not answering" in manager.prompt_note("pixel") and "blocked" in manager.prompt_note("pixel")
    flag.unlink()
    manager.refresh()
    wait_for(lambda: manager.server("fake")["ready"])


@needs_sdk
def test_a_resource_probe_works_too(make_manager, tmp_path):
    flag = tmp_path / "closed.flag"
    manager = make_manager(cfg(env={"FAKE_FLAG": str(flag)}, probe={"resource": "fake://state"}))
    wait_for(lambda: manager.server("fake")["ready"])
    flag.write_text("x")
    manager.refresh()
    wait_for(lambda: not manager.server("fake")["ready"])


@needs_sdk
def test_refresh_rediscovers_tools_without_restarting_anything(make_manager):
    manager = make_manager(cfg())
    before = manager.server("fake")["tools_total"]
    assert "late_tool" not in manager.server("fake")["tool_names"]
    assert manager.call("fake", "grow", {}) == "added late_tool"
    manager.refresh()
    wait_for(lambda: manager.server("fake")["tools_total"] == before + 1)
    assert manager.call("fake", "late_tool", {}) == "late"


@needs_sdk
def test_a_server_that_dies_is_reconnected_by_itself(make_manager):
    manager = make_manager(cfg())
    with pytest.raises(McpCallError):
        manager.call("fake", "die", {})
    wait_for(lambda: manager.server("fake")["status"] in {"reconnecting", "disconnected"}, 15)
    wait_for(lambda: manager.server("fake")["status"] == "connected", 40)
    assert manager.call("fake", "add", {"a": 1, "b": 1}) == "2"


@needs_sdk
def test_reconnect_opens_a_fresh_connection(make_manager):
    manager = make_manager(cfg())
    first = manager.server("fake")["connected_at"]
    time.sleep(1.1)
    view = manager.reconnect("fake")
    assert view["status"] == "connected" and view["connected_at"] > first
    with pytest.raises(KeyError):
        manager.reconnect("nosuch")


@needs_sdk
def test_a_missing_command_is_offline_with_a_helpful_sentence(make_manager):
    conf = cfg("gone", command="definitely-not-installed-mcp-cmd", args=[], env={"SECRET": "abc123"})
    manager = make_manager(conf, connect=False)
    view = wait_for(lambda: (v := manager.server("gone"))["error_kind"] and v)
    assert view["error_kind"] == "not_found" and "definitely-not-installed-mcp-cmd" in view["error"]
    assert view["ready"] is False and view["tools_total"] == 0
    assert "abc123" not in json.dumps(view)
    assert manager.tools_for_agent("pixel") == []
    assert manager.register_for_agent("pixel") == []
    assert "offline" in manager.prompt_note("pixel") and "blocked" in manager.prompt_note("pixel")


@needs_sdk
def test_a_disabled_server_is_never_started(make_manager):
    manager = make_manager(cfg(enabled=False), connect=False)
    time.sleep(0.5)
    view = manager.server("fake")
    assert view["status"] == "disabled" and view["tools_total"] == 0 and manager.register_for_agent("pixel") == []


def test_before_start_everything_reads_disconnected_and_nothing_is_running():
    manager = MCPManager([cfg()])
    assert manager.running() is False
    assert manager.server("fake")["status"] == "disconnected"
    assert manager.tools_for_agent("pixel") == [] and manager.prompt_note("pixel")
    manager.stop()  # a no-op


# --------------------------------------------------------------------------------------------------
# Streamable HTTP and SSE
# --------------------------------------------------------------------------------------------------


@needs_sdk
def test_connects_over_streamable_http(make_manager, http_server):
    port = http_server("streamable-http")
    manager = make_manager(cfg("web", transport="streamable-http", url=f"http://127.0.0.1:{port}/mcp"))
    view = manager.server("web")
    assert view["transport"] == "streamable-http" and view["target"] == f"http://127.0.0.1:{port}/mcp"
    assert manager.call("web", "add", {"a": 20, "b": 22}) == "42"


@needs_sdk
def test_streamable_http_with_headers_uses_a_configured_client(make_manager, http_server, monkeypatch):
    monkeypatch.setenv("T_AUTH", "Bearer abc")
    port = http_server("streamable-http")
    manager = make_manager(cfg("web", transport="streamable-http", url=f"http://127.0.0.1:{port}/mcp", headers={"Authorization": "${T_AUTH}"}))
    assert manager.call("web", "add", {"a": 1, "b": 2}) == "3"
    assert "Bearer" not in json.dumps(manager.status())


@needs_sdk
def test_connects_over_sse(make_manager, http_server):
    port = http_server("sse")
    manager = make_manager(cfg("old", transport="sse", url=f"http://127.0.0.1:{port}/sse"))
    assert manager.call("old", "add", {"a": 3, "b": 4}) == "7"


@needs_sdk
def test_nothing_listening_is_offline_and_the_url_is_shown_without_credentials(make_manager):
    port = free_port()
    conf = cfg("web", transport="streamable-http", url=f"http://user:pw@127.0.0.1:{port}/mcp?key=k3y", connect_timeout=10)
    manager = make_manager(conf, connect=False)
    view = wait_for(lambda: (v := manager.server("web"))["error_kind"] and v, 30)
    assert view["error_kind"] == "refused"
    blob = json.dumps(view)
    assert "pw" not in blob and "k3y" not in blob and f"127.0.0.1:{port}" in blob


@needs_sdk
def test_a_server_that_appears_later_is_picked_up_by_refresh(make_manager, http_server):
    port = free_port()
    manager = make_manager(cfg("late", transport="streamable-http", url=f"http://127.0.0.1:{port}/mcp", connect_timeout=10), connect=False)
    wait_for(lambda: manager.server("late")["error_kind"], 30)
    real = http_server("streamable-http")  # a different port: point the config at it
    manager._servers["late"].cfg.url = f"http://127.0.0.1:{real}/mcp"  # what editing mcp_servers.json does
    view = manager.refresh("late", wait=30)
    assert {s["id"]: s for s in view}["late"]["status"] == "connected"


# --------------------------------------------------------------------------------------------------
# Binding tools to cats
# --------------------------------------------------------------------------------------------------


@needs_sdk
def test_only_the_bound_cat_gets_the_tools_and_only_for_its_own_turn(make_manager):
    manager = make_manager(cfg(bind_to=["pixel"]))
    assert manager.servers_for_agent("pixel") == ["fake"] and manager.servers_for_agent("pulse") == []
    assert manager.register_for_agent("pulse") == []  # Pulse: nothing
    assert deferred.extra_tool_schemas() == []
    names = manager.register_for_agent("pixel")
    assert "mcp__fake__add" in names
    registered = {s["function"]["name"] for s in deferred.extra_tool_schemas()}
    assert registered == set(names)
    assert deferred.execute_loaded_tool("mcp__fake__add", {"a": 4, "b": 4}) == "8"
    # the next cat's turn starts from an empty registry (the engine clears it per run)
    deferred.clear_loaded_tools()
    assert manager.register_for_agent("pulse") == [] and deferred.extra_tool_schemas() == []


@needs_sdk
def test_register_agent_tools_adds_mcp_tools_beside_the_registry_tools(make_manager):
    from backend.platform.tool_registry import register_agent_tools

    mcp_service.set_manager(make_manager(cfg(bind_to=["pixel"])))
    assert "mcp__fake__add" in register_agent_tools("pixel")
    deferred.clear_loaded_tools()
    assert not any(n.startswith("mcp__") for n in register_agent_tools("pulse"))
    assert register_agent_tools("") == []


@needs_sdk
def test_engine_dispatch_runs_a_bound_mcp_tool(make_manager):
    from backend import tools

    manager = make_manager(cfg(bind_to=["pixel"]))
    manager.register_for_agent("pixel")
    token = tools.set_tool_allowlist({"read_file"})  # the cat's built-ins only: MCP tools ride on the per-turn registry
    try:
        assert tools.execute_tool("mcp__fake__add", {"a": 6, "b": 7}) == "13"
    finally:
        tools.reset_tool_allowlist(token)
    deferred.clear_loaded_tools()
    token = tools.set_tool_allowlist({"read_file"})
    try:
        assert "not available" in tools.execute_tool("mcp__fake__add", {"a": 1, "b": 1})
    finally:
        tools.reset_tool_allowlist(token)


@needs_sdk
def test_a_1to1_chat_schema_of_the_bound_cat_lists_the_tools(make_manager, isolated):
    from backend.platform.catcrew import seed_catcrew
    from backend.tools import tools_schema_for

    seed_catcrew()
    mcp_service.set_manager(make_manager(cfg(bind_to=["pixel"])))
    pixel = {s["function"]["name"] for s in tools_schema_for(project_id="p", bot_id="pixel")}
    assert "mcp__fake__add" in pixel and "read_file" in pixel
    deferred.clear_loaded_tools()
    pulse = {s["function"]["name"] for s in tools_schema_for(project_id="p", bot_id="pulse")}
    assert not any(n.startswith("mcp__") for n in pulse)


@needs_sdk
def test_a_big_server_shows_a_few_tools_and_the_rest_through_find_and_call(make_manager):
    manager = make_manager(cfg(max_tools=6, priority=["add", "peek"], env={"FAKE_MANY": "30"}))
    view = manager.server("fake")
    assert view["tools_total"] > 30 and view["tools_exposed"] == 6 and view["tools_on_demand"] == view["tools_total"] - 6
    names = manager.register_for_agent("pixel")
    assert len(names) == 6 + 2 and "mcp_find_tools" in names and "mcp_call_tool" in names
    assert "mcp__fake__add" in names and "mcp__fake__peek" in names
    found = deferred.execute_loaded_tool("mcp_find_tools", {"query": "bulk tool number 17"})
    assert "fake/bulk_17" in found and "bulk_16" not in found
    assert deferred.execute_loaded_tool("mcp_call_tool", {"server": "fake", "tool": "bulk_17", "arguments": {}}) == "bulk 17"
    assert "no MCP server" in deferred.execute_loaded_tool("mcp_call_tool", {"server": "other", "tool": "x"})
    # a cat the server is not bound to cannot reach the hidden tools either
    assert "no MCP server" in manager.call_bound("pulse", {"server": "fake", "tool": "bulk_17"})
    assert "No matching" in manager.find_tools("pulse", {"query": "bulk"})


@needs_sdk
def test_the_prompt_note_names_the_tools_and_the_playbook(make_manager):
    manager = make_manager(cfg(skill="blender-mcp"))
    note = manager.prompt_note("pixel")
    assert "mcp__fake__*" in note and "blender-mcp" in note and "read_skill" in note
    assert manager.prompt_note("pulse") == ""


# --------------------------------------------------------------------------------------------------
# Approvals
# --------------------------------------------------------------------------------------------------


@needs_sdk
def test_mcp_tools_need_approval_unless_they_declare_themselves_read_only(make_manager, isolated):
    manager = make_manager(cfg())
    mcp_service.set_manager(manager)
    approvals.set_approval_required(False)
    assert approvals.needs_approval("mcp__fake__add") is False  # the switch is off
    approvals.set_approval_required(True)
    assert approvals.needs_approval("mcp__fake__add") is True
    assert approvals.needs_approval("mcp__fake__peek") is False  # readOnlyHint
    assert approvals.needs_approval("read_file") is False
    # the on-demand door is judged by the tool behind it
    asked: list[str] = []
    token = approvals.set_approval_hook(lambda name, args: asked.append(name) or "deny")
    try:
        assert "Denied" in (approvals.gate_tool("mcp_call_tool", {"server": "fake", "tool": "add", "arguments": {}}) or "")
        assert approvals.gate_tool("mcp_call_tool", {"server": "fake", "tool": "peek"}) is None
    finally:
        approvals.reset_approval_hook(token)
    assert asked == ["mcp__fake__add"]


# --------------------------------------------------------------------------------------------------
# The compile-check tool (Unity)
# --------------------------------------------------------------------------------------------------

CONSOLE = (
    "[Error] Assets/Scripts/Player.cs(12,9): error CS1002: ; expected\n"
    "[Error] Assets/Scripts/Player.cs(12,9): error CS1002: ; expected\n"
    "[Error] Assets/Scripts/Enemy/Enemy.cs(3,14): error CS0246: The type or namespace name 'Rigidbody3' could not be found\n"
    "[Error] Library/PackageCache/x/Foo.cs(1,1): error CS0103: The name 'y' does not exist\n"
    "[Log] Reloading assemblies\n"
)


def test_compile_errors_are_parsed_once_each_and_windows_paths_are_normalised():
    errs = mcp_features.parse_compile_errors(CONSOLE + "C:\\Work\\Game\\Assets\\Scripts\\Ui.cs(5,1): error CS1513: } expected")
    assert [(e["file"], e["line"], e["code"]) for e in errs] == [
        ("Assets/Scripts/Player.cs", 12, "CS1002"),
        ("Assets/Scripts/Enemy/Enemy.cs", 3, "CS0246"),
        ("Library/PackageCache/x/Foo.cs", 1, "CS0103"),
        ("Assets/Scripts/Ui.cs", 5, "CS1513"),
    ]
    assert mcp_features.parse_compile_errors("all quiet") == []


def test_the_report_puts_the_source_around_each_error_and_sets_package_noise_aside(tmp_path, monkeypatch):
    scripts = tmp_path / "Assets" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "Player.cs").write_text("\n".join(f"line {i}" for i in range(1, 30)), encoding="utf-8")
    monkeypatch.setattr("backend.services.workspace.current_workspace", lambda: tmp_path)
    report = mcp_features.build_report(mcp_features.parse_compile_errors(CONSOLE), source_root="Assets/Scripts")
    assert "Assets/Scripts/Player.cs:12:9 CS1002" in report
    assert ">  12| line 12" in report and "   11| line 11" in report
    assert "1 more error(s) are outside Assets/Scripts" in report
    assert "replace_in_file" in report and "call this tool again" in report
    assert mcp_features.build_report([]).startswith("Compiles clean")


@needs_sdk
def test_unity_compile_check_refreshes_reads_the_console_and_reports(make_manager, tmp_path, monkeypatch):
    console = tmp_path / "console.txt"
    console.write_text(CONSOLE, encoding="utf-8")
    scripts = tmp_path / "proj" / "Assets" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "Player.cs").write_text("using UnityEngine;\nclass P {\n  int x = 3\n}\n" * 4, encoding="utf-8")
    monkeypatch.setattr("backend.services.workspace.current_workspace", lambda: tmp_path / "proj")
    feature = {
        "tool_name": "unity_compile_check", "refresh_tool": "refresh_unity", "refresh_args": {"mode": "force", "compile": "request"},
        "console_tool": "read_console", "console_args": {"action": "get"}, "source_root": "Assets/Scripts", "settle_seconds": 0,
    }
    manager = make_manager(cfg("unity", bind_to=["pulse"], env={"FAKE_CONSOLE": str(console)}, features={"compile_check": feature}))
    names = manager.register_for_agent("pulse")
    assert "unity_compile_check" in names
    schema = next(s for s in deferred.extra_tool_schemas() if s["function"]["name"] == "unity_compile_check")
    assert "recompile" in schema["function"]["description"].lower()
    out = deferred.execute_loaded_tool("unity_compile_check", {})
    assert "Assets/Scripts/Player.cs:12:9 CS1002" in out and "Enemy.cs:3:14 CS0246" in out
    console.write_text("Nothing to see.", encoding="utf-8")
    assert deferred.execute_loaded_tool("unity_compile_check", {"refresh": False}).startswith("Compiles clean")
    # not bound to another cat
    deferred.clear_loaded_tools()
    assert "unity_compile_check" not in manager.register_for_agent("pixel")


@needs_sdk
def test_compile_check_stops_the_endless_fix_loop(make_manager, tmp_path):
    console = tmp_path / "console.txt"
    console.write_text(CONSOLE, encoding="utf-8")
    feature = {"refresh_tool": "", "console_tool": "read_console", "settle_seconds": 0}
    manager = make_manager(cfg("unity", bind_to=["pulse"], env={"FAKE_CONSOLE": str(console)}, features={"compile_check": feature}))
    manager.register_for_agent("pulse")
    outs = [deferred.execute_loaded_tool("unity_compile_check", {}) for _ in range(9)]
    assert all("CS1002" in o for o in outs[:8])
    assert outs[8].startswith("Error:") and "blocked" in outs[8]


# --------------------------------------------------------------------------------------------------
# The API
# --------------------------------------------------------------------------------------------------


@pytest.fixture
def api(isolated, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.api.app import create_app
    from backend.core.chat_service import ChatService

    monkeypatch.setenv("CATCREW_MCP", "1")
    app = create_app(chat_service=ChatService(use_memory=False), startup=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        yield client


@needs_sdk
def test_api_lists_servers_with_status_bound_cats_and_no_secrets(api, make_manager):
    from backend.platform.catcrew import seed_catcrew

    seed_catcrew()
    conf = cfg("blender", label="Blender MCP", icon="🧊", bind_to=["pixel"], env={"API_SECRET": "sekrit-9"}, skill="blender-mcp", setup_hint="run the installer")
    mcp_service.set_manager(make_manager(conf))
    res = api.get("/api/mcp/servers")
    assert res.status_code == 200
    body = res.json()
    assert body["sdk_installed"] is True and body["running"] is True and body["enabled"] is True and body["problems"] == []
    (server,) = body["servers"]
    assert server["id"] == "blender" and server["status"] == "connected" and server["ready"] is True
    assert server["icon"] == "🧊" and server["bound_to"] == ["pixel"]
    assert server["bound_cats"] == [{"id": "pixel", "name": "Pixel"}]
    assert server["setup_hint"] == "run the installer" and server["tools_total"] >= 8
    assert "sekrit" not in res.text and "env" not in server and "command" not in server


@needs_sdk
def test_api_refresh_and_reconnect(api, make_manager):
    mcp_service.set_manager(make_manager(cfg("blender")))
    before = api.get("/api/mcp/servers").json()["servers"][0]["connected_at"]
    res = api.post("/api/mcp/refresh", json={})
    assert res.status_code == 200 and res.json()["servers"][0]["status"] == "connected"
    time.sleep(1.1)
    res = api.post("/api/mcp/servers/blender/reconnect")
    assert res.status_code == 200
    body = res.json()
    assert body["server"]["status"] == "connected" and body["server"]["connected_at"] > before
    assert api.post("/api/mcp/servers/nope/reconnect").status_code == 404
    hard = api.post("/api/mcp/refresh", json={"hard": True})
    assert hard.status_code == 200 and hard.json()["servers"][0]["status"] == "connected"


@needs_sdk
def test_api_tool_listing_marks_what_is_in_the_cats_list(api, make_manager):
    mcp_service.set_manager(make_manager(cfg("blender", max_tools=3, priority=["add"], env={"FAKE_MANY": "5"})))
    rows = api.get("/api/mcp/servers/blender/tools").json()
    assert sum(1 for r in rows if r["exposed"]) == 3
    assert next(r for r in rows if r["name"] == "add")["exposed"] is True
    assert next(r for r in rows if r["name"] == "peek")["read_only"] is True
    assert api.get("/api/mcp/servers/nope/tools").status_code == 404


def test_api_says_so_when_mcp_is_switched_off(api, monkeypatch):
    mcp_service.set_manager(MCPManager([cfg("blender")]))
    monkeypatch.setenv("CATCREW_MCP", "0")
    body = api.get("/api/mcp/servers").json()
    assert body["enabled"] is False and body["running"] is False and body["servers"][0]["status"] == "disconnected"
    assert api.post("/api/mcp/refresh", json={}).status_code == 409


def test_api_reports_config_problems(api, tmp_path):
    bad = tmp_path / "m.json"
    bad.write_text(json.dumps({"servers": {"ok": {"command": "x"}, "bad": {"transport": "smoke"}}}))
    mcp_service.set_manager(MCPManager(config_path=bad))
    body = api.get("/api/mcp/servers").json()
    assert [s["id"] for s in body["servers"]] == ["ok"] and len(body["problems"]) == 1


@needs_sdk
def test_refresh_reloads_the_config_file(api, make_manager, tmp_path):
    path = tmp_path / "m.json"
    good = {"command": sys.executable, "args": [FAKE, "stdio"], "bind_to": ["pixel"], "ping_interval": 2}
    path.write_text(json.dumps({"servers": {"one": good}}))
    manager = MCPManager(config_path=path)
    mcp_service.set_manager(manager)
    manager.start()
    wait_for(lambda: manager.server("one")["status"] == "connected")
    path.write_text(json.dumps({"servers": {"one": good, "two": {**good, "label": "Second"}}}))
    body = api.post("/api/mcp/refresh", json={}).json()
    assert {s["id"] for s in body["servers"]} == {"one", "two"}
    wait_for(lambda: manager.server("two")["status"] == "connected")
    path.write_text(json.dumps({"servers": {"two": good}}))
    api.post("/api/mcp/refresh", json={})
    assert manager.server("one") is None and manager.server("two")["status"] == "connected"
