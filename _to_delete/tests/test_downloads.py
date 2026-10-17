from pathlib import Path

from backend.services.downloads import display_name, format_when, recent_downloads, record_download


def test_record_and_recent(tmp_path: Path, monkeypatch) -> None:
    import backend.services.downloads as downloads

    monkeypatch.setattr(downloads, "HISTORY_PATH", tmp_path / "downloads.json")
    record_download("bartowski/gemma#a.gguf", status="done", folder=str(tmp_path))
    record_download("unsloth/qwen#b.gguf", status="failed", error="HTTP 403")
    rows = recent_downloads(5)
    assert len(rows) == 2
    assert rows[0]["name"] == "unsloth/qwen#b.gguf"
    assert rows[0]["status"] == "failed"
    assert display_name(rows[1]["name"]) == "a.gguf"
    assert format_when(rows[0]["finished_at"])


def test_recover_interrupted_from_job_and_parts(tmp_path: Path, monkeypatch) -> None:
    import backend.services.downloads as downloads
    from backend.services.local_models import incomplete_gguf_names

    monkeypatch.setattr(downloads, "HISTORY_PATH", tmp_path / "downloads.json")
    monkeypatch.setattr(downloads, "JOB_PATH", tmp_path / "job.json")
    (tmp_path / "model.gguf.part").write_bytes(b"partial")
    downloads.save_job("google/gemma#model.gguf", ["repo#queued.gguf"])
    marked = downloads.recover_interrupted(tmp_path)
    assert any("model.gguf" in item for item in marked)
    assert any("queued.gguf" in item for item in marked)
    rows = downloads.load_history()
    assert all(item["status"] == "interrupted" for item in rows)
    assert downloads.load_job() == {}
    assert "model.gguf" in incomplete_gguf_names(tmp_path)


def test_resolve_spec_from_history_and_sidecar(tmp_path: Path, monkeypatch) -> None:
    import backend.services.downloads as downloads
    from backend.services.local_models import write_sidecar

    monkeypatch.setattr(downloads, "HISTORY_PATH", tmp_path / "downloads.json")
    record_download("bartowski/gemma#google_gemma-3-12b-it-Q4_K_M.gguf", status="interrupted")
    spec = downloads.resolve_download_spec("google_gemma-3-12b-it-Q4_K_M.gguf", tmp_path)
    assert spec == "bartowski/gemma#google_gemma-3-12b-it-Q4_K_M.gguf"
    write_sidecar(
        tmp_path / "mmproj-BF16.gguf",
        {"hf_repo": "bartowski/gemma", "mmproj": "mmproj-BF16.gguf"},
    )
    mate = downloads.resolve_download_spec("mmproj-BF16.gguf", tmp_path)
    assert mate == "bartowski/gemma#mmproj-BF16.gguf"


def test_discard_incomplete_removes_part(tmp_path: Path, monkeypatch) -> None:
    import backend.services.downloads as downloads

    monkeypatch.setattr("backend.services.local_models.models_dir", lambda data=None: tmp_path)
    part = tmp_path / "model.gguf.part"
    part.write_bytes(b"partial")
    (tmp_path / "model.gguf").write_bytes(b"nope")
    removed = downloads.discard_incomplete("repo#model.gguf", tmp_path)
    assert "model.gguf.part" in removed
    assert "model.gguf" in removed
    assert not part.exists()


def test_recover_keeps_hub_spec(tmp_path: Path, monkeypatch) -> None:
    import backend.services.downloads as downloads

    monkeypatch.setattr(downloads, "HISTORY_PATH", tmp_path / "downloads.json")
    monkeypatch.setattr(downloads, "JOB_PATH", tmp_path / "job.json")
    record_download("google/gemma#model.gguf", status="downloading")
    (tmp_path / "model.gguf.part").write_bytes(b"partial")
    downloads.save_job("", [])
    marked = downloads.recover_interrupted(tmp_path)
    assert marked == ["google/gemma#model.gguf"]
    assert downloads.load_history()[0]["name"] == "google/gemma#model.gguf"


def test_display_name() -> None:
    assert display_name("repo#file.gguf") == "file.gguf"
    assert display_name("plain") == "plain"


def test_record_keeps_hub_spec_on_filename_fail(tmp_path: Path, monkeypatch) -> None:
    import backend.services.downloads as downloads

    monkeypatch.setattr(downloads, "HISTORY_PATH", tmp_path / "downloads.json")
    record_download("bartowski/gemma#file.gguf", status="downloading")
    record_download("file.gguf", status="failed", error="nope")
    assert downloads.load_history()[0]["name"] == "bartowski/gemma#file.gguf"
    assert downloads.load_history()[0]["status"] == "failed"


def test_live_status_marks_queued(tmp_path: Path, monkeypatch) -> None:
    import backend.services.downloads as downloads

    monkeypatch.setattr(downloads, "HISTORY_PATH", tmp_path / "downloads.json")
    record_download("repo#a.gguf", status="interrupted")
    rows = downloads.with_live_status(
        downloads.recent_downloads(5),
        current="other#b.gguf",
        queued=["repo#a.gguf"],
    )
    assert rows[0]["status"] == "queued"
    assert "retry" not in str(rows[0]["status"])


def test_sibling_history_guesses_12b_repo(tmp_path: Path, monkeypatch) -> None:
    import backend.services.downloads as downloads

    monkeypatch.setattr(downloads, "HISTORY_PATH", tmp_path / "downloads.json")
    monkeypatch.setattr("backend.services.gguf_browser.guess_hub_spec", lambda *_a, **_k: "")
    record_download(
        "bartowski/google_gemma-3-4b-it-GGUF#google_gemma-3-4b-it-Q4_K_M.gguf",
        status="done",
    )
    spec = downloads.resolve_download_spec(
        "google_gemma-3-12b-it-Q4_K_M.gguf",
        tmp_path,
        guess=False,
    )
    assert spec == (
        "bartowski/google_gemma-3-12b-it-GGUF#google_gemma-3-12b-it-Q4_K_M.gguf"
    )
