from jinx_ai.llama_server import (
    choose_release_asset,
    names_from_models_payload,
    pick_cudart_asset,
    pick_release_asset,
    server_origin,
    server_v1,
)


def test_server_origin_strips_v1() -> None:
    assert server_origin("http://127.0.0.1:8787/v1") == "http://127.0.0.1:8787"
    assert server_v1("http://127.0.0.1:8787") == "http://127.0.0.1:8787/v1"


def test_pick_cuda_zip_for_nvidia() -> None:
    assets = [
        {"name": "llama-b1-bin-win-cuda-12.4-x64.zip", "browser_download_url": "cuda"},
        {"name": "llama-b1-bin-win-vulkan-x64.zip", "browser_download_url": "vk"},
        {"name": "llama-b1-bin-win-cpu-x64.zip", "browser_download_url": "cpu"},
        {"name": "llama-b1-bin-ubuntu-x64.zip", "browser_download_url": "linux"},
        {"name": "cudart-llama-bin-win-cuda-12.4-x64.zip", "browser_download_url": "cudart"},
    ]
    cuda = pick_release_asset(assets, cuda=True)
    assert cuda is not None
    assert cuda["name"].startswith("llama-")
    assert "cuda" in cuda["name"]
    vulkan = pick_release_asset(assets, cuda=False)
    assert vulkan is not None
    assert "vulkan" in vulkan["name"]
    cudart = pick_cudart_asset(assets, cuda["name"])
    assert cudart is not None
    assert cudart["browser_download_url"] == "cudart"


def test_cuda_falls_back_to_vulkan() -> None:
    assets = [
        {"name": "llama-b1-bin-win-vulkan-x64.zip", "browser_download_url": "vk"},
        {"name": "llama-b1-bin-win-cpu-x64.zip", "browser_download_url": "cpu"},
    ]
    picked = pick_release_asset(assets, cuda=True)
    assert picked is not None
    assert "vulkan" in picked["name"]


def test_github_latest_docs_release_is_skipped() -> None:
    latest = {"tag_name": "v0.4.0", "assets": [{"name": "Source code.zip"}]}
    builds = {
        "tag_name": "b10881",
        "prerelease": True,
        "assets": [
            {"name": "cudart-llama-bin-win-cuda-12.4-x64.zip"},
            {"name": "llama-b10881-bin-win-cuda-12.4-x64.zip", "browser_download_url": "ok"},
        ],
    }
    picked = choose_release_asset([latest, builds], cuda=True)
    assert picked is not None
    asset, _assets = picked
    assert asset["browser_download_url"] == "ok"


def test_github_latest_alone_has_no_windows_zip() -> None:
    latest = {"tag_name": "v0.4.0", "assets": [{"name": "v0.4.0.tar.gz"}]}
    assert choose_release_asset([latest], cuda=True) is None


def test_router_models_payload_only_counts_vram() -> None:
    payload = {
        "data": [
            {"id": "Meta-Llama-3.1-8B-Instruct-Q4_K_M", "status": {"value": "unloaded"}},
            {"id": "google_gemma-3-4b-it-Q4_K_M", "status": {"value": "loaded"}},
        ]
    }
    assert names_from_models_payload(payload) == ["google_gemma-3-4b-it-Q4_K_M"]
    assert names_from_models_payload(payload, loaded_only=False) == [
        "Meta-Llama-3.1-8B-Instruct-Q4_K_M",
        "google_gemma-3-4b-it-Q4_K_M",
    ]


def test_legacy_models_payload_without_status_keeps_all() -> None:
    payload = {"data": [{"id": "only-one.gguf"}]}
    assert names_from_models_payload(payload) == ["only-one.gguf"]


def test_ctx_from_router_args() -> None:
    from jinx_ai.llama_server import ctx_from_models_payload

    payload = {
        "data": [
            {
                "id": "gemma",
                "status": {"value": "loaded", "args": ["llama-server", "--ctx-size", "8192"]},
                "meta": {"n_ctx": 8192},
            }
        ]
    }
    assert ctx_from_models_payload(payload) == 8192
