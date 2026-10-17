from pathlib import Path

from backend.services.local_models import family_from_name, is_mmproj_name, move_models_dir, parse_quant, same_volume, scan_gguf, write_sidecar


def test_parse_quant_and_family() -> None:
    assert parse_quant("gemma-3-12b-it-Q4_K_M.gguf") == "Q4_K_M"
    assert parse_quant("model-IQ4_XS.gguf") == "IQ4_XS"
    assert family_from_name("gemma-3-12b-it-Q4_K_M.gguf") == "gemma-3-12b-it"
    assert is_mmproj_name("gemma-3-12b-it-mmproj-f16.gguf")


def test_scan_skips_part_and_attaches_mmproj(tmp_path: Path) -> None:
    folder = tmp_path / "models"
    folder.mkdir()
    gguf = folder / "gemma-3-12b-it-Q4_K_M.gguf"
    gguf.write_bytes(b"GGUF" + b"\x00" * 12)
    (folder / "gemma-3-12b-it-mmproj-f16.gguf").write_bytes(b"GGUF" + b"\x00" * 12)
    (folder / "half.gguf.part").write_bytes(b"nope")
    write_sidecar(gguf, {"hf_repo": "bartowski/gemma", "quant": "Q4_K_M"})
    found = scan_gguf(folder)
    assert len(found) == 1
    assert found[0].name == "gemma-3-12b-it-Q4_K_M"
    assert found[0].quant == "Q4_K_M"
    assert found[0].hf_repo == "bartowski/gemma"
    assert "mmproj" in found[0].mmproj.lower()


def test_scan_skips_truncated_and_part_sibling(tmp_path: Path) -> None:
    from backend.services.local_models import incomplete_gguf_names, scan_gguf

    folder = tmp_path / "models"
    folder.mkdir()
    good = folder / "ok-Q4_K_M.gguf"
    good.write_bytes(b"GGUF" + b"\x00" * 20)
    bad = folder / "half-Q4_K_M.gguf"
    bad.write_bytes(b"nope")
    (folder / "pull-Q4_K_M.gguf.part").write_bytes(b"partial")
    found = scan_gguf(folder)
    assert [item.name for item in found] == ["ok-Q4_K_M"]
    leftover = incomplete_gguf_names(folder)
    assert "half-Q4_K_M.gguf" in leftover
    assert "pull-Q4_K_M.gguf" in leftover


def test_same_volume_and_move(tmp_path: Path) -> None:
    src = tmp_path / "old"
    dest = tmp_path / "new"
    src.mkdir()
    (src / "a.gguf").write_bytes(b"abc")
    (src / "a.json").write_text("{}", encoding="utf-8")
    (src / "skip.gguf.part").write_bytes(b"x")
    assert same_volume(src, dest)
    stopped = []
    move_models_dir(src, dest, stop=lambda: stopped.append(True))
    assert stopped == [True]
    assert (dest / "a.gguf").is_file()
    assert (dest / "a.json").is_file()
    assert not (dest / "skip.gguf.part").exists()
    assert not (src / "a.gguf").exists()
