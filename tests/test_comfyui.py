from pathlib import Path

from jinx_ai.comfyui import (
    apply_overrides,
    build_headless_command,
    comfy_display_origin,
    desktop_exe,
    extra_paths_config,
    hidden_creationflags,
    img2img_workflow,
    maybe_stop_comfy,
    sampler_for_checkpoint,
    stop_comfyui,
    txt2img_workflow,
    ui_to_api,
    upscale_workflow,
    _CREATE_NO_WINDOW,
)
from jinx_ai.tools import ASSISTANT_TOOL_NAMES, tools_schema_for


def test_txt2img_and_img2img_graphs() -> None:
    graph = txt2img_workflow("sd_xl_turbo_1.0_fp16.safetensors", "a cyan orb", seed=1)
    assert graph["4"]["inputs"]["ckpt_name"].endswith(".safetensors")
    assert graph["6"]["inputs"]["text"] == "a cyan orb"
    assert graph["3"]["inputs"]["seed"] == 1
    assert sampler_for_checkpoint("sd_xl_turbo_1.0")["steps"] <= 8
    edited = img2img_workflow("model.safetensors", "same", "photo.png", denoise=0.4, seed=2)
    assert edited["10"]["class_type"] == "LoadImage"
    assert edited["10"]["inputs"]["image"] == "photo.png"
    assert edited["3"]["inputs"]["latent_image"] == ["11", 0]
    assert edited["3"]["inputs"]["denoise"] == 0.4


def test_upscale_graph_uses_scale_or_model() -> None:
    lanczos = upscale_workflow("in.png", scale=2)
    assert lanczos["2"]["class_type"] == "ImageScaleBy"
    modeled = upscale_workflow("in.png", model="4x.pth")
    assert modeled["2"]["class_type"] == "UpscaleModelLoader"
    assert modeled["3"]["class_type"] == "ImageUpscaleWithModel"


def test_ui_to_api_and_overrides() -> None:
    ui = {
        "last_node_id": 2,
        "nodes": [
            {
                "id": 6,
                "type": "CLIPTextEncode",
                "mode": 0,
                "inputs": [{"name": "clip", "link": 1}],
                "widgets_values": ["hello"],
            },
            {
                "id": 4,
                "type": "CheckpointLoaderSimple",
                "mode": 0,
                "inputs": [],
                "widgets_values": ["model.safetensors"],
            },
        ],
        "links": [[1, 4, 1, 6, 0, "CLIP"]],
    }
    info = {
        "CLIPTextEncode": {"input": {"required": {"text": ["STRING"], "clip": ["CLIP"]}}},
        "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": ["model.safetensors"]}}},
    }
    prompt = ui_to_api(ui, info)
    assert prompt["6"]["inputs"]["clip"] == ["4", 1]
    assert prompt["6"]["inputs"]["text"] == "hello"
    assert prompt["4"]["inputs"]["ckpt_name"] == "model.safetensors"
    patched = apply_overrides(prompt, {"6.text": "new prompt"})
    assert patched["6"]["inputs"]["text"] == "new prompt"


def test_comfy_tools_in_assistant_schema() -> None:
    names = {item["function"]["name"] for item in tools_schema_for("assistant")}
    assert names == set(ASSISTANT_TOOL_NAMES)
    for name in (
        "generate_image",
        "comfy_info",
        "comfy_list",
        "comfy_run_workflow",
        "comfy_upload",
        "upscale_image",
        "remove_background",
        "comfy_interrupt",
        "comfy_stop",
    ):
        assert name in names


def test_desktop_exe_detects_install() -> None:
    exe = desktop_exe()
    if exe is not None:
        assert exe.name.lower().endswith(".exe")
        assert exe.is_file()


def test_hidden_flags_hide_the_window() -> None:
    flags = hidden_creationflags()
    if flags:
        assert flags & _CREATE_NO_WINDOW
        assert not flags & 0x00000008  # DETACHED_PROCESS


def test_headless_command_uses_venv_not_desktop(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "ComfyUI"
    root.mkdir()
    (root / "main.py").write_text("# comfy\n", encoding="utf-8")
    py = root / ".venv" / "Scripts" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")
    models = tmp_path / "models"
    (models / "checkpoints").mkdir(parents=True)
    monkeypatch.setattr("jinx_ai.comfyui.install_root", lambda: root)
    monkeypatch.setattr("jinx_ai.comfyui.shared_root", lambda: models)
    monkeypatch.setattr("jinx_ai.comfyui.desktop_settings", lambda: {})
    monkeypatch.setattr("jinx_ai.comfyui.brains_settings", lambda: {"comfyui_url": ""})
    monkeypatch.setattr("jinx_ai.comfyui.BASE_DIR", tmp_path)
    cmd, cwd = build_headless_command()
    joined = " ".join(cmd).lower()
    assert "comfy desktop.exe" not in joined
    assert cmd[0] == str(py)
    assert "main.py" in cmd
    assert "--disable-auto-launch" in cmd
    assert "--listen" in cmd
    assert cwd == root
    extra = extra_paths_config()
    assert extra is not None
    text = extra.read_text(encoding="utf-8")
    assert "checkpoints:" in text
    assert models.as_posix() in text


def test_stop_leaves_foreign_server_alone(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("jinx_ai.comfyui._state_path", lambda: tmp_path / "comfy.pid")
    monkeypatch.setattr("jinx_ai.comfyui.ping_comfyui", lambda: True)
    monkeypatch.setattr("jinx_ai.comfyui.comfy_origin", lambda refresh=False: "http://127.0.0.1:8188")
    killed = []
    monkeypatch.setattr("jinx_ai.comfyui._kill_pid", lambda pid: killed.append(pid))
    msg = stop_comfyui()
    assert not killed
    assert "no hidden" in msg.lower()


def test_maybe_stop_skips_sticky(tmp_path: Path, monkeypatch) -> None:
    pid_file = tmp_path / "comfy.pid"
    pid_file.write_text(
        '{"pid": 4242, "python": "C:\\\\py.exe", "sticky": true}',
        encoding="utf-8",
    )
    monkeypatch.setattr("jinx_ai.comfyui._state_path", lambda: pid_file)
    monkeypatch.setattr("jinx_ai.comfyui.owned_by_jinx", lambda: True)
    monkeypatch.setattr("jinx_ai.comfyui.brains_settings", lambda: {"comfyui_autostop": True})
    monkeypatch.setattr("jinx_ai.comfyui.stop_comfyui", lambda: "killed")
    assert maybe_stop_comfy() == ""


def test_maybe_stop_respects_autostop_off(tmp_path: Path, monkeypatch) -> None:
    pid_file = tmp_path / "comfy.pid"
    pid_file.write_text(
        '{"pid": 4242, "python": "C:\\\\py.exe", "sticky": false}',
        encoding="utf-8",
    )
    monkeypatch.setattr("jinx_ai.comfyui._state_path", lambda: pid_file)
    monkeypatch.setattr("jinx_ai.comfyui.owned_by_jinx", lambda: True)
    monkeypatch.setattr("jinx_ai.comfyui.brains_settings", lambda: {"comfyui_autostop": False})
    monkeypatch.setattr("jinx_ai.comfyui.stop_comfyui", lambda: "killed")
    assert maybe_stop_comfy() == ""


def test_brains_autostop_defaults_on() -> None:
    from jinx_ai.settings_store import brains_settings

    cfg = brains_settings({"brains": {}})
    assert cfg["comfyui_autostop"] is True


def test_launch_spawns_hidden_python(tmp_path: Path, monkeypatch) -> None:
    import jinx_ai.comfyui as comfy

    root = tmp_path / "ComfyUI"
    root.mkdir()
    (root / "main.py").write_text("# comfy\n", encoding="utf-8")
    py = root / ".venv" / "Scripts" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    monkeypatch.setattr(comfy, "install_root", lambda: root)
    monkeypatch.setattr(comfy, "shared_root", lambda: None)
    monkeypatch.setattr(comfy, "desktop_settings", lambda: {})
    monkeypatch.setattr(comfy, "brains_settings", lambda: {"comfyui_url": ""})
    monkeypatch.setattr(comfy, "comfy_origin", lambda url="", refresh=False: "http://127.0.0.1:8188")
    monkeypatch.setattr(comfy, "ping_comfyui", lambda: False)
    monkeypatch.setattr(comfy, "_ping", lambda origin: False)
    monkeypatch.setattr(comfy, "BASE_DIR", tmp_path)
    spawned = {}

    class _Proc:
        pid = 99

    def _fake_popen(cmd, **kwargs):
        spawned["cmd"] = cmd
        spawned["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(comfy.subprocess, "Popen", _fake_popen)
    msg = comfy.launch_comfyui()
    assert "hidden" in msg.lower()
    assert "Comfy Desktop.exe" not in " ".join(spawned["cmd"])
    flags = spawned["kwargs"].get("creationflags", 0)
    if flags:
        assert flags & _CREATE_NO_WINDOW
    state = (tmp_path / "data" / "comfy.pid").read_text(encoding="utf-8")
    assert "99" in state
    assert '"sticky":true' in state.replace(" ", "")


def test_display_origin_does_not_hit_the_network(monkeypatch) -> None:
    import jinx_ai.comfyui as comfy

    monkeypatch.setattr(comfy, "_origin_cache", "")
    monkeypatch.setattr(comfy, "brains_settings", lambda: {"comfyui_url": "http://127.0.0.1:8188"})

    def boom(*_a, **_k):
        raise AssertionError("display origin must not HTTP")

    monkeypatch.setattr(comfy, "_http_get", boom)
    monkeypatch.setattr(comfy, "_ping", boom)
    assert comfy_display_origin() == "http://127.0.0.1:8188"


def test_ping_bypasses_env_proxy(monkeypatch) -> None:
    import jinx_ai.comfyui as comfy

    assert comfy._http.trust_env is False
    seen: dict = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        raise ConnectionError("offline")

    monkeypatch.setattr(comfy._http, "get", fake_get)
    assert comfy._ping("http://127.0.0.1:8188") is False
    assert "8188" in seen["url"]
