from jinx_ai.hardware import HardwareInfo, format_hardware, probe_hardware


def test_prompt_line_and_format() -> None:
    info = HardwareInfo(
        cpu="AMD Ryzen 9 9950X",
        cores=16,
        ram_gb=32.0,
        gpu="NVIDIA GeForce RTX 5080",
        vram_gb=16.0,
    )
    line = info.prompt_line()
    assert line.startswith("This PC:")
    assert "RTX 5080" in line
    assert "16 GB VRAM" in line
    assert "32 GB RAM" in line
    text = format_hardware(info)
    assert "CPU: AMD Ryzen 9 9950X" in text
    assert "VRAM: 16.0 GB" in text


def test_probe_uses_nvidia_smi(monkeypatch) -> None:
    import jinx_ai.hardware as hardware

    monkeypatch.setattr(hardware, "_nvidia_smi", lambda: "nvidia-smi")
    monkeypatch.setattr(
        hardware,
        "_run",
        lambda cmd, timeout=4.0: "NVIDIA GeForce RTX 5080, 16303",
    )
    monkeypatch.setattr(hardware, "_cpu_name", lambda: "AMD Ryzen 9 9950X")
    monkeypatch.setattr(hardware, "_ram_gb", lambda: 32.0)
    monkeypatch.setattr("os.cpu_count", lambda: 16)
    hardware._cache = None
    info = probe_hardware(ttl=0)
    assert "RTX 5080" in info.gpu
    assert info.vram_gb == 15.9
    assert info.cores == 16
    again = probe_hardware(ttl=60)
    assert again is info


def test_system_prompt_has_this_pc(monkeypatch) -> None:
    fake = HardwareInfo("AMD Ryzen 9 9950X", 16, 32.0, "NVIDIA GeForce RTX 5080", 16.0)
    monkeypatch.setattr("jinx_ai.hardware.probe_hardware", lambda ttl=120: fake)
    from jinx_ai.tools import build_system_prompt

    text = build_system_prompt("developer")
    assert "This PC:" in text
    assert "RTX 5080" in text
    assert "hardware_info" in text
    assert "recommend_models" in text
