from jinx_ai.docker_runtime import DockerInfo, format_docker, probe_docker


def test_prompt_line_when_available() -> None:
    info = DockerInfo(
        available=True,
        engine="29.7.2",
        compose="5.5.0",
        os_name="Docker Desktop",
    )
    line = info.prompt_line()
    assert line.startswith("Docker:")
    assert "29.7.2" in line
    assert "Compose 5.5.0" in line
    assert "read_skill docker" in line


def test_prompt_line_when_missing() -> None:
    info = DockerInfo(available=False, error="docker not on PATH")
    assert "not available" in info.prompt_line()


def test_format_docker_missing() -> None:
    text = format_docker(DockerInfo(available=False, error="nope"))
    assert "not available" in text
    assert "nope" in text


def test_probe_caches(monkeypatch) -> None:
    import jinx_ai.docker_runtime as dr

    calls = {"n": 0}

    def fake_run(cmd, timeout=4.0):
        calls["n"] += 1
        if cmd[:2] == ["docker", "version"]:
            return 0, "29.7.2"
        if cmd[:3] == ["docker", "compose", "version"]:
            return 0, "5.5.0"
        if cmd[:2] == ["docker", "context"]:
            return 0, "desktop-linux"
        if cmd[:2] == ["docker", "info"]:
            return 0, "Docker Desktop"
        return 1, "unexpected"

    monkeypatch.setattr(dr, "docker_bin", lambda: "docker")
    monkeypatch.setattr(dr, "_run", fake_run)
    dr._cache = None
    first = probe_docker(ttl=0)
    assert first.available
    assert first.engine == "29.7.2"
    n_after = calls["n"]
    again = probe_docker(ttl=60)
    assert again is first
    assert calls["n"] == n_after


def test_system_prompt_mentions_docker(monkeypatch) -> None:
    from jinx_ai.docker_runtime import DockerInfo
    from jinx_ai.hardware import HardwareInfo
    from jinx_ai.tools import build_system_prompt

    monkeypatch.setattr(
        "jinx_ai.hardware.probe_hardware",
        lambda ttl=120: HardwareInfo("CPU", 8, 16.0, "GPU", 8.0),
    )
    monkeypatch.setattr(
        "jinx_ai.docker_runtime.probe_docker",
        lambda ttl=120: DockerInfo(True, "29.7.2", "5.5.0", os_name="Docker Desktop"),
    )
    text = build_system_prompt("developer")
    assert "Docker:" in text
    assert "29.7.2" in text
    assert "docker_info" in text
