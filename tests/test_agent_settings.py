from backend.config import AGENT_MAX_ROUNDS, COMMAND_TIMEOUT
from backend.services.settings_store import agent_settings, save_agent_settings
from backend.tools import tools_schema_for


def test_agent_settings_defaults() -> None:
    cfg = agent_settings({"agent": {}})
    assert cfg["command_timeout"] == max(15, min(900, COMMAND_TIMEOUT))
    assert cfg["max_rounds"] == max(0, min(80, AGENT_MAX_ROUNDS))


def test_agent_settings_clamp(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.services.settings_store.SETTINGS_PATH", tmp_path / "settings.json")
    saved = save_agent_settings({"command_timeout": 5, "max_rounds": 999})
    assert saved["command_timeout"] == 15
    assert saved["max_rounds"] == 80
    loaded = agent_settings()
    assert loaded["command_timeout"] == 15
    assert loaded["max_rounds"] == 80
    zero = save_agent_settings({"max_rounds": 0})
    assert zero["max_rounds"] == 0


def test_run_command_schema_uses_live_timeout(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.services.settings_store.SETTINGS_PATH", tmp_path / "settings.json")
    save_agent_settings({"command_timeout": 240})
    schema = tools_schema_for("developer")
    run = next(item for item in schema if item["function"]["name"] == "run_command")
    assert "240s" in run["function"]["description"]


def test_agent_settings_live_values(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.services.settings_store.SETTINGS_PATH", tmp_path / "settings.json")
    save_agent_settings({"command_timeout": 300, "max_rounds": 40})
    cfg = agent_settings()
    assert cfg["command_timeout"] == 300
    assert cfg["max_rounds"] == 40
