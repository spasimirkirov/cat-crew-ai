from backend.services.gguf_browser import HfFile, HubCard, card_from_payload, diversify_cards, filter_cards, infer_tools, infer_vision, is_recommended, matching_mmproj, paginate, pick_quant, vram_budget_gb


def _file(name: str, size_gb: float, mmproj: bool = False) -> HfFile:
    return HfFile(
        repo="bartowski/gemma",
        filename=name,
        size_bytes=int(size_gb * 1_000_000_000),
        quant=name.split("-")[-1].replace(".gguf", "") if not mmproj else "",
        is_mmproj=mmproj,
    )


def test_vram_budget() -> None:
    assert vram_budget_gb(16) == 11.2


def test_pick_quant_prefers_q4_when_it_fits() -> None:
    files = [
        _file("model-Q8_0.gguf", 14),
        _file("model-Q4_K_M.gguf", 8),
        _file("model-Q3_K_M.gguf", 6),
        _file("model-mmproj-f16.gguf", 0.6, mmproj=True),
    ]
    chosen = pick_quant(files, vram_gb=16)
    assert chosen is not None
    assert chosen.quant == "Q4_K_M"
    assert chosen.recommended


def test_pick_quant_falls_back_when_q4_is_too_big() -> None:
    files = [
        _file("model-Q4_K_M.gguf", 20),
        _file("model-Q3_K_M.gguf", 5),
    ]
    chosen = pick_quant(files, vram_gb=8)
    assert chosen is not None
    assert chosen.quant == "Q3_K_M"


def test_matching_mmproj() -> None:
    files = [
        _file("gemma-3-12b-it-Q4_K_M.gguf", 8),
        _file("gemma-3-12b-it-mmproj-f16.gguf", 0.6, mmproj=True),
    ]
    gguf = files[0]
    mate = matching_mmproj(files, gguf)
    assert mate is not None
    assert mate.is_mmproj


def test_infer_vision_and_tools() -> None:
    files = [_file("gemma-3-12b-it-Q4_K_M.gguf", 8), _file("gemma-3-12b-it-mmproj-f16.gguf", 0.6, mmproj=True)]
    assert infer_vision("bartowski/gemma-3-12b-it-GGUF", ["gguf"], files)
    assert infer_tools("bartowski/Qwen2.5-Instruct-GGUF", ["gguf", "instruct"])
    assert not infer_tools("someone/all-minilm-embed-GGUF", [])


def test_filter_cards_defaults() -> None:
    vision = HubCard(
        repo="a/vl",
        title="vl",
        fits_gpu=True,
        has_vision=True,
        has_tools=True,
        vram_gb=7.0,
    )
    fat = HubCard(
        repo="a/fat",
        title="fat",
        fits_gpu=False,
        has_vision=True,
        has_tools=True,
    )
    text_only = HubCard(
        repo="a/text",
        title="text",
        fits_gpu=True,
        has_vision=False,
        has_tools=True,
    )
    kept = filter_cards([vision, fat, text_only], gpu=True, vision=True, tools=True)
    assert [item.repo for item in kept] == ["a/vl"]
    assert filter_cards([vision, fat, text_only], gpu=False, vision=False, tools=False) == [
        vision,
        fat,
        text_only,
    ]


def test_is_recommended_filter() -> None:
    pick = HubCard(
        repo="bartowski/gemma-3-12b-it-GGUF",
        title="gemma-3-12b-it-GGUF",
        author="bartowski",
        trusted=True,
        fits_gpu=True,
        has_vision=True,
        has_tools=True,
    )
    coder = HubCard(
        repo="unsloth/Qwen2.5-Coder-14B-Instruct-GGUF",
        title="Qwen2.5-Coder-14B-Instruct-GGUF",
        author="unsloth",
        trusted=True,
        fits_gpu=True,
        has_vision=False,
        has_tools=True,
    )
    random = HubCard(
        repo="someone/random-7b-GGUF",
        title="random-7b-GGUF",
        trusted=False,
        fits_gpu=True,
        has_tools=True,
        has_vision=True,
    )
    fat = HubCard(
        repo="bartowski/gemma-3-27b-it-GGUF",
        title="gemma-3-27b-it-GGUF",
        author="bartowski",
        trusted=True,
        fits_gpu=False,
        has_tools=True,
        has_vision=True,
    )
    assert is_recommended(pick)
    assert is_recommended(coder)
    assert not is_recommended(random)
    assert not is_recommended(fat)
    kept = filter_cards(
        [pick, coder, random, fat],
        gpu=False,
        vision=False,
        tools=False,
        recommended=True,
    )
    assert [item.repo for item in kept] == [pick.repo, coder.repo]


def test_diversify_cards_mixes_families() -> None:
    from backend.services.gguf_browser import card_family

    cards = [
        HubCard(repo="bartowski/Qwen2.5-7B-Instruct-GGUF", title="Qwen2.5-7B", trusted=True, fits_gpu=True, has_tools=True),
        HubCard(repo="bartowski/Qwen2.5-14B-Instruct-GGUF", title="Qwen2.5-14B", trusted=True, fits_gpu=True, has_tools=True),
        HubCard(repo="bartowski/Qwen2.5-32B-Instruct-GGUF", title="Qwen2.5-32B", trusted=True, fits_gpu=True, has_tools=True),
        HubCard(repo="bartowski/gemma-3-12b-it-GGUF", title="gemma-3-12b-it-GGUF", trusted=True, fits_gpu=True, has_tools=True),
        HubCard(repo="bartowski/Llama-3.1-8B-Instruct-GGUF", title="Llama-3.1-8B-Instruct-GGUF", trusted=True, fits_gpu=True, has_tools=True),
    ]
    mixed = diversify_cards(cards, per_family=2)
    families = [card_family(item) for item in mixed]
    assert families.count("qwen") == 2
    assert "gemma" in families
    assert "llama" in families
    assert families[:3] == ["qwen", "gemma", "llama"]


def test_paginate_and_card_from_payload() -> None:
    rows = [HubCard(repo=f"a/{i}", title=str(i)) for i in range(13)]
    page, idx, total = paginate(rows, 1, size=6)
    assert idx == 1
    assert total == 3
    assert [item.title for item in page] == ["6", "7", "8", "9", "10", "11"]
    card = card_from_payload(
        {
            "id": "bartowski/gemma-3-12b-it-GGUF",
            "downloads": 9,
            "likes": 2,
            "pipeline_tag": "image-text-to-text",
            "description": "Gemma 3 instruct GGUF with vision and tool calling.",
            "cardData": {"pretty_name": "Gemma 3 12B IT"},
            "tags": ["gguf", "gemma3"],
            "siblings": [
                {"rfilename": "gemma-3-12b-it-Q4_K_M.gguf", "size": 8_000_000_000},
                {"rfilename": "gemma-3-12b-it-mmproj-f16.gguf", "size": 600_000_000},
            ],
        },
        vram_gb=16,
    )
    assert card is not None
    assert card.fits_gpu
    assert card.has_vision
    assert card.rec is not None
    assert card.vram_gb > 0
    assert "Gemma 3 instruct" in card.blurb


def test_is_gated_and_help() -> None:
    from backend.services.gguf_browser import gated_help, hub_error_url, is_gated, repo_page

    assert is_gated(True)
    assert is_gated("auto")
    assert is_gated("manual")
    assert not is_gated(False)
    assert not is_gated("")
    assert repo_page("meta-llama/Meta-Llama-3.1-8B") == "https://huggingface.co/meta-llama/Meta-Llama-3.1-8B"
    missing = gated_help("meta-llama/Meta-Llama-3.1-8B", has_token=False)
    assert "Settings → API Access" in missing
    assert "https://huggingface.co/meta-llama/Meta-Llama-3.1-8B" in missing
    denied = gated_help("meta-llama/Meta-Llama-3.1-8B", has_token=True, status=403)
    assert "HTTP 403" in denied
    assert "accept" in denied.lower()
    assert hub_error_url(denied) == "https://huggingface.co/meta-llama/Meta-Llama-3.1-8B"


def test_card_from_payload_gated_auto() -> None:
    card = card_from_payload(
        {
            "id": "meta-llama/Meta-Llama-3.1-8B-Instruct-GGUF",
            "gated": "auto",
            "siblings": [{"rfilename": "model-Q4_K_M.gguf", "size": 5_000_000_000}],
        },
        vram_gb=16,
    )
    assert card is not None
    assert card.gated
    public = card_from_payload(
        {
            "id": "bartowski/public-GGUF",
            "gated": False,
            "siblings": [{"rfilename": "model-Q4_K_M.gguf", "size": 5_000_000_000}],
        },
        vram_gb=16,
    )
    assert public is not None
    assert not public.gated


def test_public_download_headers_omit_token(monkeypatch) -> None:
    from backend.services import gguf_browser

    monkeypatch.setattr("backend.services.api_access.huggingface_token", lambda data=None: "hf_secret")
    public = gguf_browser.huggingface_headers(use_token=False)
    assert "Authorization" not in public
    gated = gguf_browser.huggingface_headers(use_token=True)
    assert gated["Authorization"] == "Bearer hf_secret"


def test_download_gguf_fails_fast_when_gated_without_token(monkeypatch, tmp_path) -> None:
    from backend.services import gguf_browser

    monkeypatch.setattr(gguf_browser, "probe_gated", lambda repo: True)
    monkeypatch.setattr("backend.services.api_access.huggingface_token", lambda data=None: "")
    try:
        gguf_browser.download_gguf("meta-llama/Meta-Llama-3.1-8B", "a.gguf", dest_dir=tmp_path)
    except RuntimeError as err:
        text = str(err)
        assert "Settings → API Access" in text
        assert "https://huggingface.co/meta-llama/Meta-Llama-3.1-8B" in text
        return
    raise AssertionError("expected gated RuntimeError")


def test_http_download_retries_with_token_on_403(monkeypatch, tmp_path) -> None:
    from backend.services import gguf_browser

    calls: list[bool] = []

    class FakeResp:
        def __init__(self, status: int, body: bytes = b"") -> None:
            self.status_code = status
            self.headers = {"Content-Length": str(len(body))}
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def iter_content(self, chunk_size=1):
            if self._body:
                yield self._body

    def fake_get(url, stream=False, timeout=None, headers=None):
        use_auth = "Authorization" in (headers or {})
        calls.append(use_auth)
        if not use_auth:
            return FakeResp(403)
        return FakeResp(200, b"gguf")

    monkeypatch.setattr(gguf_browser.requests, "get", fake_get)
    monkeypatch.setattr("backend.services.api_access.huggingface_token", lambda data=None: "hf_secret")
    dest = tmp_path / "a.gguf"
    gguf_browser._http_download("pub/model", "a.gguf", dest, None, lambda: False, use_token=False)
    assert calls == [False, True]
    assert dest.read_bytes() == b"gguf"


def test_http_download_keeps_part_when_stream_ends_early(monkeypatch, tmp_path) -> None:
    from backend.services import gguf_browser

    class FakeResp:
        status_code = 200
        headers = {"Content-Length": "20"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=1):
            yield b"GGUF"

    monkeypatch.setattr(gguf_browser.requests, "get", lambda *a, **k: FakeResp())
    dest = tmp_path / "a.gguf"
    try:
        gguf_browser._http_download("pub/model", "a.gguf", dest, None, lambda: False)
    except RuntimeError as err:
        assert "Incomplete" in str(err)
        assert not dest.exists()
        assert dest.with_name("a.gguf.part").is_file()
        return
    raise AssertionError("expected incomplete RuntimeError")
